import pytest
from fastapi.testclient import TestClient
from api.index import app

client=TestClient(app)

def test_invalid_answer_rejected():
    response=client.post('/api/py/check',json={'answer':'short'})
    assert response.status_code==422

def test_setup_fails_readably(monkeypatch):
    monkeypatch.delenv('BIFROST_URL',raising=False)
    response=client.post('/api/py/check',json={'answer':'A sufficiently long answer with facts.'})
    assert response.status_code==503
    assert 'configured' in response.json()['detail']

def test_unknown_run_has_no_internal_error():
    assert client.get('/api/py/runs/not-a-uuid').status_code==404

def test_model_list_is_bounded():
    assert len(client.get('/api/py/models').json()['models'])==3
    response=client.post('/api/py/ask',json={'question':'Test question','models':['unapproved-model']})
    assert response.status_code==422
