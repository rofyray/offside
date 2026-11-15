import asyncio
from fastapi import FastAPI, Request, HTTPException
from backend.core.http import health, guard, get_run, events
from backend.core.models import CheckRequest, AskRequest, CheckedClaim
from pydantic import Field
from backend.core.watchlist import router, internal_request, reserve_credits, BudgetExhausted
from backend.core.telemetry import Run
from backend.core.config import SetupError
from backend.core import llm
from backend.agents.offside_graph import check

app = FastAPI(title='OffSide API',docs_url='/api/py/docs',openapi_url='/api/py/openapi.json')
app.include_router(router)

@app.middleware('http')
async def private_watchlist(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/py/watchlist'):
        response.headers['Cache-Control'] = 'private, no-store'
    return response

app.get('/api/py/health')(health)
app.get('/api/py/runs/{run_id}')(get_run)
MODELS = ['claude-haiku-4-5','openai/gpt-6-luna','claude-sonnet-5-5']

@app.post('/api/py/check')
async def check_route(body: CheckRequest, request: Request):
    await guard(request,'offside')
    async def execute(emit):
        await check(body,Run('offside',emit))
    return events(execute)

class VerifyRequest(CheckRequest):
    watch_claims: list[CheckedClaim] = Field(default_factory=list, max_length=8)

@app.post('/api/py/verify')
async def verify_route(body: VerifyRequest, request: Request):
    monitoring = internal_request(request)
    if not monitoring:
        await guard(request,'offside')
    async def emit(event,data):
        pass
    try:
        async with asyncio.timeout(280):
            run = Run('offside',emit)
            if monitoring:
                run.credit_guard = reserve_credits
                run.watch_claims = body.watch_claims
                body.live = False
            return await check(body,run)
    except BudgetExhausted:
        raise HTTPException(429,'Daily monitoring budget reached.')
    except SetupError:
        raise HTTPException(503,'Live research is not configured yet.')
    except Exception:
        raise HTTPException(502,'The answer could not be checked. Please try again.')

@app.get('/api/py/models')
async def models():
    return {'models':MODELS}

@app.post('/api/py/ask')
async def ask_route(body: AskRequest, request: Request):
    if len(set(body.models)) != len(body.models) or any(m not in MODELS for m in body.models):
        raise HTTPException(422,'Choose up to three distinct models from the available list.')
    await guard(request,'offside')
    async def execute(emit):
        run = Run('offside',emit)
        await run.emit('run.started',mode='ask')
        async def answer(model):
            async def relay(event,data):
                if event in ('run.started','run.completed','run.telemetry'):
                    return
                await emit(event,{**data,'model':model})
            child = Run('offside',relay)
            child.search_tasks = run.search_tasks
            try:
                text = await llm.call(child,'Ask any model','Answer the user question directly. Do not use tools.',body.question,model=model)
                await run.emit('model.answer',model=model,text=text,latency_ms=child.elapsed())
                result = await check(CheckRequest(answer=text[:6000],question=body.question,live=False),child)
                await run.emit('model.result',model=model,result=result)
                return model,result
            except Exception:
                await run.emit('model.error',model=model,message='This model could not finish. Try it again.')
                return model,None
            finally:
                run.telemetry.calls.extend(child.telemetry.calls)
                for field in ('calls','cache_hits','fallbacks'):
                    setattr(run.telemetry.bifrost,field,getattr(run.telemetry.bifrost,field)+getattr(child.telemetry.bifrost,field))
                for field in ('searches','extracts','cache_hits','credits'):
                    setattr(run.telemetry.tavily,field,getattr(run.telemetry.tavily,field)+getattr(child.telemetry.tavily,field))
        results = await asyncio.gather(*(answer(m) for m in body.models))
        rows = [{'model':m,'freshness':r.freshness,'stale':r.counts['offside'],'total':len(r.claims)} for m,r in results if r]
        if not rows:
            raise ValueError('All models failed')
        rows.sort(key=lambda r:r['freshness'] if r['freshness'] is not None else -1,reverse=True)
        await run.emit('run.telemetry',telemetry=run.finish())
        await run.emit('leaderboard.ready',rows=rows)
        await run.emit('run.completed',rows=rows)
    return events(execute)
