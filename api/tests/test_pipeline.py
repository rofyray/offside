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


@pytest.mark.asyncio
async def test_interrupted_claim_cancels_conflict_review(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from backend.core.models import CheckedClaim, Source
    started, stopped = asyncio.Event(), asyncio.Event()
    claims=[Claim(id=f'c{i}',text='A claim about the current price.',span=(0,32)) for i in range(2)]
    source=Source(id='s1',url='https://example.com',domain='example.com',title='Price')
    async def verify(claim,*args):
        if claim.id=='c1':
            await started.wait()
            raise asyncio.CancelledError()
        return CheckedClaim(**claim.model_dump(),sources=[source],verdict=Verdict(label='unverifiable',confidence=.1))
    async def conflict(*args):
        started.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    monkeypatch.setattr(pipeline,'collect_evidence',AsyncMock(return_value=[]))
    monkeypatch.setattr(pipeline,'extract_evidence',AsyncMock(return_value={}))
    monkeypatch.setattr(pipeline,'verify_claim',verify)
    monkeypatch.setattr(pipeline,'review_conflict',conflict)
    with pytest.raises(asyncio.CancelledError):
        await pipeline.investigate({'request':CheckRequest(answer=claims[0].text),'run':Run('offside',AsyncMock()),'claims':claims})
    assert stopped.is_set(), 'Conflict review must not outlive the cancelled answer check'
