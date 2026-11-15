"""Run inside the Linux service image with TEST_REDIS_URL pointing at isolated Redis."""
import hashlib
import json
import os
import time
from uuid import uuid4

import httpx
import pathway as pw
from redis import Redis
from watchtower.pipeline import build_pipeline, content_changed
from watchtower.processor import Processor
from watchtower.repository import WatchStore, PREFIX
from watchtower.tests.test_monitor import result, OWNER, URL

redis = Redis.from_url(os.environ['TEST_REDIS_URL'], decode_responses=True)
redis.flushdb()
os.environ.update(PATHWAY_TOKEN='test-token', TAVILY_API_KEY='test-key', OFFSIDE_URL='https://offside.example.com')
store = WatchStore(redis)
initial = result()
watch = store.create(OWNER, initial['run_id'], initial, [URL])
source = {'text': 'Price: ten dollars.'}
rechecks = []
observed = []


def controlled_endpoints(request):
    if request.url.host == 'api.tavily.com':
        return httpx.Response(200, json={'results':[{'url':URL,'raw_content':source['text']}]})
    rechecks.append(json.loads(request.content))
    return httpx.Response(200, json=result('outdated'))


processor = Processor(store, httpx.Client(transport=httpx.MockTransport(controlled_endpoints)))


class ControlledSource(pw.io.python.ConnectorSubject):
    def __init__(self):
        super().__init__(session_type='upsert')
        self.error = None

    def run(self):
        try:
            for text in ('Price: ten dollars.', 'Price: ten dollars.', 'Price: twenty dollars.'):
                source['text'] = text
                redis.delete('offside:pw:source:'+hashlib.sha256(URL.encode()).hexdigest())
                redis.zadd(PREFIX+'due',{watch['id']:0})
                token=uuid4().hex
                assert store.claim(token)==watch['id']
                current=store.get(watch['id'])
                snapshot, successful=processor.snapshot(current)
                row=dict(watch_id=watch['id'],lease=token,baseline=json.dumps(current['fingerprints']),snapshot=json.dumps(snapshot),successful=successful)
                self.next(**row)
                self.commit()
                deadline=time.monotonic()+15
                while redis.get(PREFIX+'lease:'+watch['id']):
                    if time.monotonic()>deadline:raise AssertionError('Pathway output did not commit')
                    time.sleep(.03)
            self.delete(**row)
            self.commit()
        except Exception as exc:
            self.error=exc


def output(row):
    observed.append(row['changed'])
    processor.process(row)


subject=ControlledSource()
build_pipeline(subject,output)
pw.run(monitoring_level=pw.MonitoringLevel.NONE)
if subject.error:raise subject.error
assert observed==[False,False,True],observed
assert len(rechecks)==1,rechecks
saved=store.get(watch['id'])
assert saved['events'][0]['kind']=='went_offside',saved
assert not content_changed('{}',json.dumps({URL:'first'}))
assert not content_changed(json.dumps({URL:'old'}),json.dumps({URL:'old','https://example.com/new':'first'}))
print('PASS: real Pathway upsert stream → baseline → unchanged → changed → one recheck → Went Offside event → deletion')
