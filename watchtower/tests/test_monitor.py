import copy
import hashlib
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from redis import Redis
from watchtower.repository import WatchStore, PREFIX, DURATION
from watchtower.processor import Processor, BudgetExhausted, public_url
from watchtower.service import create_app

URL = 'https://example.com/product'
OWNER = 'a'*64

def result(label='current'):
    return {'run_id':str(uuid4()), 'answer':'The product costs ten dollars per month.', 'freshness':100 if label=='current' else 0,
            'counts':{'onside':int(label=='current'),'offside':int(label=='outdated')},
            'claims':[{'id':'c1','text':'The product costs ten dollars per month.','verdict':{'label':label},
                       'sources':[{'url':URL}]}]}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        # Tests require an explicitly selected isolated database; never use application credentials.
        self.redis = Redis.from_url(os.environ['TEST_REDIS_URL'], decode_responses=True)
        self.redis.flushdb()
        self.store = WatchStore(self.redis)
        self.env = patch.dict(os.environ, PATHWAY_TOKEN='test-token', TAVILY_API_KEY='test-key', OFFSIDE_URL='https://offside.example.com', PATHWAY_DAILY_CREDITS='100')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.original = result()
        self.redis.set('offside:run:'+self.original['run_id'], json.dumps(self.original), ex=60)

    def watch(self, owner=OWNER):
        return self.store.create(owner,self.original['run_id'],self.original,[URL])

    def claim(self, watch, baseline=None):
        if baseline is not None:
            watch['fingerprints']=baseline
            self.redis.set(PREFIX+'watch:'+watch['id'],json.dumps(watch),keepttl=True)
        self.redis.zadd(PREFIX+'due',{watch['id']:0})
        token=uuid4().hex
        self.assertEqual(self.store.claim(token),watch['id'])
        return token

    def test_ownership_idempotency_quota_and_retention(self):
        watch=self.watch()
        self.assertEqual(self.watch()['id'],watch['id'])
        self.assertEqual(self.store.list('b'*64),[])
        self.assertFalse(self.store.stop('b'*64,watch['id']))
        self.assertGreater(self.redis.ttl('offside:run:'+self.original['run_id']),DURATION-3)
        for _ in range(4):
            self.store.create(OWNER,str(uuid4()),result(),[URL])
        with self.assertRaises(ValueError):self.store.create(OWNER,str(uuid4()),result(),[URL])
        self.assertTrue(self.store.stop(OWNER,watch['id']))
        self.assertIsNone(self.store.get(watch['id']))

    def test_global_quota_concurrent(self):
        def create(i):
            try:self.store.create(hashlib.sha256(str(i).encode()).hexdigest(),str(uuid4()),result(),[URL]);return True
            except ValueError:return False
        with ThreadPoolExecutor(12) as pool:results=list(pool.map(create,range(35)))
        self.assertEqual(sum(results),20)

    def test_atomic_budget(self):
        with ThreadPoolExecutor(12) as pool:allowed=list(pool.map(lambda _:self.store.reserve(3),range(100)))
        self.assertEqual(sum(allowed),33)
        self.assertTrue(self.store.reserve(1))
        self.assertFalse(self.store.reserve(1))

    def test_deleted_watch_cannot_be_resurrected(self):
        watch=self.watch();token=self.claim(watch)
        self.store.stop(OWNER,watch['id'])
        replacement=self.watch()
        self.assertFalse(self.store.commit(watch,token))
        self.assertEqual(self.store.get(replacement['id'])['status'],'starting')

    def test_expiry_and_exclusive_lease(self):
        watch=self.watch();token=self.claim(watch)
        self.assertIsNone(self.store.claim('other'))
        self.redis.delete(PREFIX+'watch:'+watch['id'])
        self.assertFalse(self.store.commit(watch,token))
        self.assertEqual(self.store.list(OWNER),[])

    def test_snapshot_cache_and_failed_source(self):
        calls=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(200,json={'results':[{'url':URL,'raw_content':'Price ten dollars.'}]})
        p=Processor(self.store,httpx.Client(transport=httpx.MockTransport(handle)))
        watch=self.watch();first,n=p.snapshot(watch);second,n2=p.snapshot(watch)
        self.assertEqual((first,n),(second,n2));self.assertEqual(len(calls),1)
        cached=self.redis.get('offside:tv:x:'+hashlib.sha256(URL.encode()).hexdigest())
        self.assertEqual(json.loads(cached),'Price ten dollars.')
        self.redis.delete('offside:pw:source:'+hashlib.sha256(URL.encode()).hexdigest())
        p.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'results':[]})))
        with self.assertRaises(ValueError):p.snapshot(watch)

    def test_changed_source_rechecks_and_records_transition(self):
        watch=self.watch();token=self.claim(watch,{URL:'old'})
        requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(200,json=result('outdated'))
        p=Processor(self.store,httpx.Client(transport=httpx.MockTransport(handle)))
        p.process({'watch_id':watch['id'],'lease':token,'snapshot':json.dumps({URL:'new'}),'successful':1,'changed':True})
        saved=self.store.get(watch['id'])
        self.assertEqual(saved['events'][0]['kind'],'went_offside')
        self.assertEqual(saved['events'][0]['changes'][0]['from'],'current')
        self.assertEqual(saved['fingerprints'],{URL:'new'})
        self.assertEqual(requests[0].headers['x-pathway-token'],'test-token')
        self.assertIn('watch_claims',json.loads(requests[0].content))

    def test_first_baseline_and_unchanged_do_not_recheck(self):
        watch=self.watch();token=self.claim(watch)
        p=Processor(self.store,httpx.Client(transport=httpx.MockTransport(lambda r:self.fail('Unexpected recheck'))))
        p.process({'watch_id':watch['id'],'lease':token,'snapshot':json.dumps({URL:'same'}),'successful':1,'changed':False})
        saved=self.store.get(watch['id']);self.assertEqual(saved['events'],[])
        self.assertEqual(saved['fingerprints'],{URL:'same'})
        token=self.claim(saved)
        p.process({'watch_id':watch['id'],'lease':token,'snapshot':json.dumps({URL:'same'}),'successful':1,'changed':False})
        self.assertEqual(self.store.get(watch['id'])['events'],[])

    def test_failed_recheck_preserves_baseline_for_retry(self):
        for status in (429,503):
            watch=self.watch();token=self.claim(watch,{URL:'old'})
            p=Processor(self.store,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status))))
            p.process({'watch_id':watch['id'],'lease':token,'snapshot':json.dumps({URL:'new'}),'successful':1,'changed':True})
            saved=self.store.get(watch['id'])
            self.assertEqual(saved['fingerprints'],{URL:'old'})
            self.assertEqual(saved['events'],[])
            self.assertEqual(saved['status'],'budget_paused' if status==429 else 'retrying')

    def test_api_authorization_validation_and_ownership(self):
        c=TestClient(create_app(self.store));headers={'Authorization':'Bearer test-token'}
        self.assertEqual(c.get('/watchlists/'+OWNER).status_code,401)
        self.assertEqual(c.get('/watchlists/invalid',headers=headers).status_code,422)
        self.assertEqual(c.post('/budget',headers=headers,json={'credits':-1}).status_code,422)
        response=c.post('/watchlists/'+OWNER,headers=headers,json={'run_id':self.original['run_id']})
        self.assertEqual(response.status_code,201,response.text)
        self.assertNotIn('owner',response.json());self.assertNotIn('fingerprints',response.json())
        wid=response.json()['id']
        self.assertEqual(c.delete('/watchlists/'+'b'*64+'/'+wid,headers=headers).status_code,404)
        self.assertEqual(c.delete('/watchlists/'+OWNER+'/'+wid,headers=headers).status_code,200)
        self.assertEqual(c.get('/watchlists/'+OWNER,headers=headers).json()['watches'],[])

    def test_private_sources_rejected(self):
        for url in ('http://127.0.0.1','http://169.254.169.254/latest','http://redis.railway.internal','ftp://example.com','https://x:y@example.com','https://example.com:6379'):
            self.assertFalse(public_url(url),url)
        self.assertTrue(public_url(URL))


if __name__=='__main__':unittest.main()
