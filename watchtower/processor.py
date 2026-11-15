import hashlib
import ipaddress
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from .repository import INTERVAL


class BudgetExhausted(Exception):
    pass


def public_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return False
        if not host or '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def source_urls(result):
    return list(dict.fromkeys(s['url'] for c in result.get('claims', []) for s in c.get('sources', [])
                             if public_url(s['url'])))[:40]


def fingerprint(text):
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def verdict_changes(before, after):
    old = {' '.join(c['text'].split()).casefold(): c['verdict']['label'] for c in before['claims']}
    transitions = []
    for claim in after['claims']:
        previous = old.get(' '.join(claim['text'].split()).casefold())
        current = claim['verdict']['label']
        if previous and previous != current:
            transitions.append({'text': claim['text'], 'from': previous, 'to': current})
    return transitions


class Processor:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or httpx.Client(timeout=40, follow_redirects=False)

    def snapshot(self, watch):
        fingerprints = dict(watch['fingerprints'])
        successful = 0
        for start in range(0, len(watch['urls']), 10):
            urls = watch['urls'][start:start+10]
            missing = []
            for url in urls:
                # The scan cache is shared by watches, but never masks a change for longer than one scan interval.
                key = 'offside:pw:source:' + hashlib.sha256(url.encode()).hexdigest()
                cached = self.store.redis.get(key)
                if cached:
                    fingerprints[url] = json.loads(cached)['fingerprint']
                    successful += 1
                else:
                    missing.append(url)
            if not missing:
                continue
            # Reserve conservatively: one unit per URL, even though basic extraction bills per five successes.
            if not self.store.reserve(len(missing)):
                raise BudgetExhausted()
            response = self.client.post('https://api.tavily.com/extract',
                headers={'Authorization': 'Bearer '+os.environ['TAVILY_API_KEY']},
                json={'urls': missing, 'extract_depth': 'basic', 'include_usage': True})
            response.raise_for_status()
            for item in response.json().get('results', []):
                url, text = item.get('url'), item.get('raw_content', '')
                if url not in missing or not text.strip():
                    continue
                digest = hashlib.sha256(url.encode()).hexdigest()
                fingerprints[url] = fingerprint(text)
                successful += 1
                self.store.redis.set('offside:pw:source:'+digest, json.dumps({'fingerprint': fingerprints[url]}), ex=INTERVAL-60)
                self.store.redis.set('offside:tv:x:'+digest, json.dumps(text[:18000]), ex=43200)
        if not successful:
            raise ValueError('No sources could be read.')
        # Missing sources retain their old fingerprints. Failed extraction is not a content change.
        return fingerprints, successful

    def process(self, row):
        watch = self.store.get(row['watch_id'])
        if not watch:
            return
        token = row['lease']
        try:
            current = json.loads(row['snapshot'])
            now = time.time()
            watch['last_scan_at'] = now
            watch['next_scan_at'] = now + INTERVAL
            watch['status'] = 'watching'
            watch['message'] = 'Sources scanned; no content changes detected.'
            if row['changed']:
                response = self.client.post(os.environ['OFFSIDE_URL'].rstrip('/')+'/api/py/verify',
                    headers={'X-Pathway-Token': os.environ['PATHWAY_TOKEN']},
                    json={'answer': watch['latest_result']['answer'], 'prefer_official': True, 'live': False, 'watch_claims': watch['latest_result']['claims']}, timeout=290)
                if response.status_code == 429:
                    raise BudgetExhausted()
                response.raise_for_status()
                result = response.json()
                if not result.get('run_id') or not isinstance(result.get('claims'), list):
                    raise ValueError('Invalid verification response')
                if not result['claims'] or all(c['verdict']['label'] == 'unverifiable' for c in result['claims']):
                    raise ValueError('Recheck could not settle any claims')
                transitions = verdict_changes(watch['latest_result'], result)
                stale = any(t['from'] in ('current', 'partially_current') and t['to'] in ('outdated', 'contradicted') for t in transitions)
                watch['events'].insert(0, {'at': now, 'kind': 'went_offside' if stale else 'rechecked',
                    'run_id': result['run_id'], 'freshness': result['freshness'], 'changes': transitions})
                watch['events'] = watch['events'][:20]
                watch['latest_result'] = result
                watch['last_recheck_at'] = now
                # Keep watching the original cited sources and any new evidence, up to the pipeline's bounded source set.
                watch['urls'] = list(dict.fromkeys(watch['urls'] + source_urls(result)))[:40]
                watch['message'] = 'A source changed. The answer was checked again.'
            elif not watch['fingerprints']:
                watch['message'] = 'Source baseline saved. Watching for changes.'
            if row['successful'] < len(watch['urls']):
                watch['status'] = 'partial'
                watch['message'] += ' Some sources were unavailable; they will be retried.'
                watch['next_scan_at'] = now + 3600
            watch['fingerprints'] = current
            self.store.commit(watch, token)
        except BudgetExhausted:
            self.fail(watch, token, budget=True)
        except Exception:
            self.fail(watch, token)

    def fail(self, watch, token, budget=False):
        if budget:
            tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
            watch['next_scan_at'] = datetime.combine(tomorrow, datetime.min.time(), timezone.utc).timestamp() + 60
            watch['status'] = 'budget_paused'
            watch['message'] = 'Daily monitoring budget reached. Scans resume tomorrow.'
        else:
            watch['next_scan_at'] = time.time() + 3600
            watch['status'] = 'retrying'
            watch['message'] = 'A source scan or recheck could not finish. Retrying within an hour.'
        self.store.commit(watch, token)
