"""Redis polling input → Pathway content comparison → bounded verification output."""
import json
import logging
import threading
import time
from uuid import uuid4

import pathway as pw
from .processor import BudgetExhausted


def content_changed(baseline: str, snapshot: str) -> bool:
    before, after = json.loads(baseline), json.loads(snapshot)
    return any(url in before and before[url] != digest for url, digest in after.items())


class Snapshot(pw.Schema):
    watch_id: str = pw.column_definition(primary_key=True)
    lease: str
    baseline: str
    snapshot: str
    successful: int


class WatchSubject(pw.io.python.ConnectorSubject):
    def __init__(self, processor):
        super().__init__(session_type='upsert')
        self.processor = processor
        self.stop_event = threading.Event()
        self.heartbeat = 0.0

    def run(self):
        sent = {}
        pending = None
        while not self.stop_event.is_set():
            self.heartbeat = time.monotonic()
            try:
                for watch_id, row in list(sent.items()):
                    if not self.processor.store.get(watch_id):
                        self.delete(**row)
                        del sent[watch_id]
                token = uuid4().hex
                busy = pending and self.processor.store.redis.get('offside:pw:lease:'+pending[0]) == pending[1]
                watch_id = None if busy else self.processor.store.claim(token)
                if watch_id:
                    watch = self.processor.store.get(watch_id)
                    if watch:
                        try:
                            snapshot, successful = self.processor.snapshot(watch)
                            row = dict(watch_id=watch_id, lease=token, baseline=json.dumps(watch['fingerprints']),
                                       snapshot=json.dumps(snapshot), successful=successful)
                            self.next(**row)
                            pending = (watch_id, token)
                            sent[watch_id] = row
                        except BudgetExhausted:
                            self.processor.fail(watch, token, budget=True)
                        except Exception:
                            logging.warning('Source scan failed; retry scheduled', exc_info=False)
                            self.processor.fail(watch, token)
                self.commit()
            except Exception:
                logging.warning('Monitoring storage unavailable; retrying', exc_info=False)
            self.stop_event.wait(5)


def build_pipeline(subject, on_row):
    snapshots = pw.io.python.read(subject, schema=Snapshot, autocommit_duration_ms=1000, max_backlog_size=20)
    changes = snapshots.with_columns(changed=pw.apply(content_changed, pw.this.baseline, pw.this.snapshot))
    def changed(key, row, time, is_addition):
        if is_addition:
            on_row(row)
    pw.io.subscribe(changes, on_change=changed)
