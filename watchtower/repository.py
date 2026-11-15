import hashlib
import json
import os
import time
from datetime import datetime, timezone
from uuid import UUID

from redis import Redis

PREFIX = 'offside:pw:'
DURATION = 7 * 86400
INTERVAL = 6 * 3600


class WatchStore:
    def __init__(self, redis):
        self.redis = redis

    def get(self, watch_id):
        raw = self.redis.get(PREFIX + 'watch:' + watch_id)
        return json.loads(raw) if raw else None

    def create(self, owner, run_id, result, urls):
        now = time.time()
        watch_id = str(UUID(hashlib.sha256((owner + run_id).encode()).hexdigest()[:32]))
        watch = {'id': watch_id, 'owner': owner, 'run_id': run_id, 'created_at': now,
                 'expires_at': now + DURATION, 'next_scan_at': now, 'last_scan_at': None,
                 'last_recheck_at': None, 'status': 'starting', 'message': 'Waiting for the first source scan.',
                 'fingerprints': {}, 'urls': urls, 'latest_result': result, 'events': []}
        script = '''
        if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
        redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[2])
        redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', ARGV[2])
        if redis.call('ZCARD', KEYS[2]) >= 20 or redis.call('ZCARD', KEYS[3]) >= 5 then return -1 end
        redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[5])
        redis.call('ZADD', KEYS[2], ARGV[3], ARGV[4])
        redis.call('ZADD', KEYS[3], ARGV[3], ARGV[4])
        redis.call('EXPIRE', KEYS[3], ARGV[5])
        redis.call('ZADD', KEYS[4], ARGV[2], ARGV[4])
        return 1
        '''
        outcome = self.redis.eval(script, 4, PREFIX+'watch:'+watch_id, PREFIX+'active',
                                  PREFIX+'owner:'+owner, PREFIX+'due', json.dumps(watch), now,
                                  now+DURATION, watch_id, DURATION)
        if outcome == -1:
            raise ValueError('The watchlist is full. Stop an existing watch and try again.')
        # A watch may start near the end of the original result's retention period.
        run_key = 'offside:run:'+run_id
        if 0 < self.redis.ttl(run_key) < DURATION:
            self.redis.expire(run_key, DURATION)
        return self.get(watch_id)

    def list(self, owner):
        key = PREFIX + 'owner:' + owner
        self.redis.zremrangebyscore(key, '-inf', time.time())
        return [w for wid in self.redis.zrange(key, 0, -1) if (w := self.get(wid)) and w['owner'] == owner]

    def stop(self, owner, watch_id):
        script = '''
        local raw = redis.call('GET', KEYS[1])
        if not raw or cjson.decode(raw).owner ~= ARGV[1] then return 0 end
        redis.call('DEL', KEYS[1], KEYS[5])
        redis.call('ZREM', KEYS[2], ARGV[2])
        redis.call('ZREM', KEYS[3], ARGV[2])
        redis.call('ZREM', KEYS[4], ARGV[2])
        return 1
        '''
        return bool(self.redis.eval(script, 5, PREFIX+'watch:'+watch_id, PREFIX+'active',
                                   PREFIX+'owner:'+owner, PREFIX+'due', PREFIX+'lease:'+watch_id,
                                   owner, watch_id))

    def claim(self, token):
        script = '''
        local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 20)
        for _,id in ipairs(ids) do
          if redis.call('EXISTS', ARGV[3]..'watch:'..id) == 0 then
            redis.call('ZREM', KEYS[1], id)
            redis.call('ZREM', KEYS[2], id)
          elseif redis.call('SET', ARGV[3]..'lease:'..id, ARGV[2], 'NX', 'EX', 600) then
            redis.call('ZADD', KEYS[1], tonumber(ARGV[1])+600, id)
            return id
          end
        end
        return false
        '''
        return self.redis.eval(script, 2, PREFIX+'due', PREFIX+'active', time.time(), token, PREFIX)

    def commit(self, watch, token):
        # A stopped, expired, or re-created watch must never be resurrected by a late response.
        script = '''
        if redis.call('EXISTS', KEYS[1]) == 0 or redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
        redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
        redis.call('ZADD', KEYS[3], ARGV[3], ARGV[4])
        redis.call('DEL', KEYS[2])
        return 1
        '''
        return bool(self.redis.eval(script, 3, PREFIX+'watch:'+watch['id'], PREFIX+'lease:'+watch['id'],
                                    PREFIX+'due', token, json.dumps(watch), watch['next_scan_at'], watch['id']))

    def reserve(self, credits):
        day = datetime.now(timezone.utc).date().isoformat()
        cap = min(100, max(0, int(os.getenv('PATHWAY_DAILY_CREDITS', '100'))))
        script = '''
        if tonumber(redis.call('GET', KEYS[1]) or '0') + tonumber(ARGV[1]) > tonumber(ARGV[2]) then return 0 end
        redis.call('INCRBY', KEYS[1], ARGV[1])
        redis.call('EXPIRE', KEYS[1], 172800)
        return 1
        '''
        return bool(self.redis.eval(script, 1, PREFIX+'budget:'+day, credits, cap))


def connect():
    if os.getenv('REDIS_URL'):
        return Redis.from_url(os.environ['REDIS_URL'], decode_responses=True, socket_timeout=5)
    return Redis(host=os.environ['REDIS_HOST'], password=os.environ['REDIS_PASSWORD'],
                 decode_responses=True, socket_timeout=5)


def public_watch(watch):
    result = watch['latest_result']
    return {k: watch[k] for k in ('id', 'run_id', 'created_at', 'expires_at', 'next_scan_at',
                                 'last_scan_at', 'last_recheck_at', 'status', 'message', 'events')} | {
        'answer': result['answer'], 'freshness': result['freshness'], 'counts': result['counts'],
        'latest_run_id': result['run_id'], 'source_count': len(watch['urls'])}
