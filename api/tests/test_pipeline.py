import pytest
from backend.agents import offside_graph as pipeline
from backend.core.models import Claims,Claim,Verdict,Quote,Correction,Citation,CheckRequest
from backend.core.telemetry import Run

@pytest.mark.asyncio
async def test_failed_claim_does_not_stop_other_claims(monkeypatch):
    monkeypatch.delenv('REDIS_URL',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    first='The price is 10 dollars.'
    second='The CEO is Unknown Person.'
    answer=first+' '+second
    async def call(run,agent,system,content,schema=None,model=None):
        if agent=='Stepover':
            return Claims(claims=[Claim(id='x',text=first,span=(0,len(first)),category='pricing',queries=['pricing']),Claim(id='y',text=second,span=(len(first)+1,len(answer)),category='leadership',queries=['leadership'])])
        if agent=='VAR':
            sid=content['sources'][0]['id']
            return Verdict(label='outdated',confidence=.95,current_value='20 dollars',quotes=[Quote(source_id=sid,text='The price is now 20 dollars per month.')],supporting_source_ids=[sid])
        if agent=='Golazo':
            return Correction(text='The price is now 20 dollars [1]. Leadership could not be verified.',citations=[Citation(n=1,source_id='c1s1')])
        raise AssertionError(agent)
    async def search(query,*args,**kwargs):
        if query=='leadership':
            raise RuntimeError('Provider failed')
        return [{'url':'https://example.com/pricing','content':'The price is now 20 dollars per month.','score':1,'published_date':'2026-11-14'}]
    async def extract(*args):return {}
    monkeypatch.setattr(pipeline.llm,'call',call)
    monkeypatch.setattr(pipeline.search,'search',search)
    monkeypatch.setattr(pipeline.search,'extract',extract)
    events=[]
    async def emit(event,data):events.append((event,data))
    result=await pipeline.check(CheckRequest(answer=answer),Run('offside',emit))
    assert result.counts['offside']==1
    assert result.counts['no_call']==1
    assert events[0][0]=='run.started'
    assert events[-1][0]=='run.completed'
    assert len([e for e in events if e[0]=='claim.verdict'])==2
    assert result.corrected.citations[0].source_id in {s.id for c in result.claims for s in c.sources}
