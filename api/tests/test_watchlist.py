import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from api.index import app
from backend.core import watchlist, search
from backend.core.telemetry import Run
from backend.agents import offside_graph
from backend.core.models import CheckRequest, CheckedClaim, Verdict, Source, Correction

client=TestClient(app)

def test_watchlist_is_private_and_cookie_owned(monkeypatch):
    calls=[]
    async def proxy(method,path,body=None):
        calls.append((method,path,body))
        return {'watches':[], 'available':True} if method=='GET' else {'id':str(uuid4())}
    monkeypatch.setattr(watchlist,'proxy',proxy)
    monkeypatch.setattr(watchlist,'guard',AsyncMock())
    monkeypatch.setenv('VERCEL','1')
    client=TestClient(app,base_url='https://testserver')
    response=client.get('/api/py/watchlist')
    assert response.headers['cache-control']=='private, no-store'
    assert not calls
    result=client.post('/api/py/watchlist',json={'run_id':str(uuid4())},headers={'Origin':'https://testserver'})
    assert result.status_code==201
    assert all(word in result.headers['set-cookie'].lower() for word in ('httponly','secure','samesite=strict','path=/api/py/watchlist'))
    client.get('/api/py/watchlist')
    assert calls[0][1]==calls[1][1]
    assert len(calls[0][1].rsplit('/',1)[-1])==64
    assert client.post('/api/py/watchlist',json={'run_id':str(uuid4())},headers={'Origin':'https://other.example'}).status_code==403
    assert TestClient(app).delete('/api/py/watchlist/'+str(uuid4())).status_code==404


def test_invalid_monitor_token_never_bypasses_public_guard(monkeypatch):
    monkeypatch.setenv('PATHWAY_TOKEN','correct-token')
    response=client.post('/api/py/verify',json={'answer':'A sufficiently long factual answer.'},headers={'X-Pathway-Token':'wrong-token'})
    assert response.status_code==401


@pytest.mark.asyncio
async def test_background_budget_fails_closed_before_tavily(monkeypatch):
    async def emit(*args):pass
    run=Run('offside',emit)
    run.credit_guard=AsyncMock(side_effect=watchlist.BudgetExhausted)
    post=AsyncMock()
    monkeypatch.setattr(search.httpx.AsyncClient,'post',post)
    with pytest.raises(watchlist.BudgetExhausted):
        await search.request('search',{'query':'test','search_depth':'advanced'},run)
    assert run.budget_exhausted
    run.credit_guard.assert_awaited_once_with(2)
    post.assert_not_awaited()
    save=AsyncMock()
    monkeypatch.setattr(offside_graph.store,'set',save)
    with pytest.raises(watchlist.BudgetExhausted):await offside_graph.persist({'result':None,'run':run})
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_recheck_keeps_claims_and_uses_changed_source_text(monkeypatch):
    text='The product costs ten dollars.'
    source=Source(id='c1s1',url='https://example.com/product',domain='example.com',title='Product',snippet='Old search snippet',score=1)
    claim=CheckedClaim(id='c1',text=text,span=(0,len(text)),category='product',queries=['product cost'],verdict=Verdict(label='current'),sources=[source])
    run=Run('offside',AsyncMock());run.watch_claims=[claim]
    observed=[]
    async def call(run,agent,system,content,schema=None,model=None):
        assert agent!='Stepover','Rechecks must preserve the original claims'
        if agent=='VAR':
            observed.extend(content['sources'])
            return Verdict(label='unverifiable',confidence=.8)
        return Correction(text='The price needs further checking.',citations=[])
    monkeypatch.setattr(offside_graph.llm,'call',call)
    monkeypatch.setattr(search,'search',AsyncMock(return_value=[]))
    monkeypatch.setattr(search,'extract',AsyncMock(return_value={str(source.url):'Price: twenty dollars.'}))
    monkeypatch.setattr(offside_graph.store,'set',AsyncMock())
    result=await offside_graph.check(CheckRequest(answer=text),run)
    assert result.claims[0].text==text
    assert observed[0]['snippet']=='Price: twenty dollars.'
    assert source.snippet=='Old search snippet'
