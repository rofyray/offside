import asyncio
import logging
from datetime import datetime, timezone
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from backend.core import llm, search
from backend.core.cache import store
from backend.core.models import Claims, Verdict, Correction, CheckResult, CheckedClaim, Claim, CheckRequest
from backend.core.telemetry import Run
from backend.core.watchlist import BudgetExhausted
from backend.core.scoring import repair_claims, score, label
from backend.core.citations import validate_quotes, validate_correction
from backend.core.rerank import rerank

class State(TypedDict, total=False):
    request: CheckRequest
    run: Run
    claims: list[Claim]
    checked: list[CheckedClaim]
    result: CheckResult

async def plan(state):
    req,run = state['request'],state['run']
    if run.watch_claims:
        claims = repair_claims(req.answer, [Claim.model_validate(c.model_dump()) for c in run.watch_claims])
        await run.emit('claims.extracted',claims=claims)
        return {'claims':claims}
    extracted = await llm.call(run,'Stepover','Extract at most eight time-sensitive factual claims. Skip opinions and timeless facts. Each text must be an exact substring with character offsets in the answer. Include one or two targeted queries and authoritative domains, without URL paths.',
                               {'answer':req.answer,'question':req.question,'today':datetime.now(timezone.utc).date()},Claims)
    claims = repair_claims(req.answer,extracted.claims)
    await run.emit('claims.extracted',claims=claims)
    return {'claims':claims}

async def collect_evidence(claim, req, run, today):
    params = {'include_domains':claim.preferred_domains,'include_domains_mode':'prefer'} if req.prefer_official and claim.preferred_domains else {}
    results = await asyncio.gather(*(search.search(q,run,live=req.live,**params) for q in claim.queries or [claim.text]),return_exceptions=True)
    flattened = [row for group in results if isinstance(group,list) for row in group]
    sources = rerank(flattened,claim.preferred_domains if req.prefer_official else [],today,prefix=claim.id+'s')
    if not sources or sources[0].score < .45 or not any(s.published_date for s in sources):
        try:
            advanced = await search.search((claim.queries or [claim.text])[0],run,live=req.live,search_depth='advanced',**params)
            sources = rerank(flattened+advanced,claim.preferred_domains,today,prefix=claim.id+'s')
        except Exception:
            logging.warning("Advanced evidence search failed; using available sources")
    if run.watch_claims:
        prior = next((c.sources for c in run.watch_claims if c.id == claim.id), [])
        seen = set()
        unique = []
        for source in [*prior, *sources]:
            url = str(source.url)
            if url not in seen:
                seen.add(url)
                unique.append(source.model_copy(deep=True))
        sources = unique[:10]
        for i, source in enumerate(sources):
            source.id = claim.id+'s'+str(i+1)
    return sources

async def extract_evidence(claims, evidence_tasks, run):
    groups = await asyncio.gather(*evidence_tasks,return_exceptions=True)
    urls = [str(s.url) for c,g in zip(claims,groups) if isinstance(g,list) and c.category in ('pricing','api_version') for s in g if s.official and (not c.stated_value or c.stated_value not in s.snippet)]
    try:
        return await search.extract(urls,run)
    except Exception:
        return {}

async def verify_claim(claim, evidence_task, extraction_task, run, today):
    try:
        sources = await evidence_task
    except Exception:
        sources = []
    needs_extract = claim.category in ('pricing','api_version') and any(s.official and (not claim.stated_value or claim.stated_value not in s.snippet) for s in sources)
    extracted = await extraction_task if needs_extract else {}
    if run.watch_claims:
        extracted = await search.extract([str(s.url) for s in sources],run)
    for source in sources:
        source.snippet = extracted.get(str(source.url)) or source.snippet
    await run.emit('evidence.ready',claim_id=claim.id,sources=sources)
    try:
        if not sources:
            raise ValueError('No sources')
        verdict = await llm.call(run,'VAR','Compare this claim to the provided evidence as of today. Give a verdict and exact supporting quotations. Only use the supplied source IDs. Include supporting_source_ids for sources that agree. Flag conflicting evidence. Unsettled evidence is unverifiable.',
                                 {'claim':claim.model_dump(),'today':today,'sources':[s.model_dump(mode='json') for s in sources]},Verdict)
        verdict = score(validate_quotes(verdict,sources),claim,sources,today)
    except Exception:
        verdict = Verdict(label='unverifiable',note='This claim could not be checked. The other claims are still being reviewed.')
        score(verdict,claim,sources,today)
    item = CheckedClaim(**claim.model_dump(),verdict=verdict,sources=sources)
    await run.emit('claim.verdict',claim_id=claim.id,verdict=verdict)
    return item

async def review_conflict(item, run, today):
    claim = Claim.model_validate(item.model_dump())
    verdict, sources = item.verdict, item.sources
    await run.emit('claim.escalated',claim_id=claim.id)
    try:
        review = await llm.call(run,'Panenka','Independently review conflicting or low-confidence evidence. Use exact quotations. Do not force a conclusion without support.',
                                {'claim':claim.model_dump(),'previous':verdict.model_dump(mode='json'),'sources':[s.model_dump(mode='json') for s in sources]},Verdict)
        review.decided_by = 'Panenka'
        review = score(validate_quotes(review,sources),claim,sources,today)
        if review.label != verdict.label or review.current_value != verdict.current_value:
            item.verdict = review
            await run.emit('claim.overturned',claim_id=claim.id,verdict=review)
            return True
    except Exception:
        logging.warning("Conflict review failed; retaining the original verdict")
    return False

async def correct_answer(req, checked, run):
    if not checked:
        return Correction(text=req.answer,citations=[])
    correction = await llm.call(run,'Golazo','Rewrite the original answer using only the supplied verified verdicts and evidence. Preserve timeless text. Explicitly mark unverifiable claims as unsettled. Cite every correction with [n] and map n to supplied source IDs. Do not introduce new factual claims.',
                                {'answer':req.answer,'claims':[c.model_dump(mode='json') for c in checked]},Correction)
    return validate_correction(correction,[s for c in checked for s in c.sources])

async def investigate(state):
    req, run, claims = state['request'], state['run'], state['claims']
    today = datetime.now(timezone.utc).date()
    evidence_tasks = [asyncio.create_task(collect_evidence(c, req, run, today)) for c in claims]
    extraction_task = asyncio.create_task(extract_evidence(claims, evidence_tasks, run))
    escalation_tasks = []

    async def review(claim, evidence_task):
        item = await verify_claim(claim, evidence_task, extraction_task, run, today)
        if item.sources and (item.verdict.confidence < .6 or item.verdict.conflict) and len(escalation_tasks) < 2:
            escalation_tasks.append(asyncio.create_task(review_conflict(item, run, today)))
        return item

    review_tasks = [asyncio.create_task(review(c, task)) for c, task in zip(claims, evidence_tasks)]
    try:
        checked = await asyncio.gather(*review_tasks)
        checked.sort(key=lambda c: c.span[0])
        correction = await correct_answer(req, checked, run)
        await run.emit('corrected.ready', **correction.model_dump())
        overturns = await asyncio.gather(*escalation_tasks)
        if any(overturns):
            correction = await correct_answer(req, checked, run)
            await run.emit('corrected.ready', **correction.model_dump())
    finally:
        # Cancel every spawned task on failure or client disconnect, including early conflict reviews.
        tasks = [*review_tasks, *evidence_tasks, extraction_task, *escalation_tasks]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    counts = {key:0 for key in ('offside','tight','onside','no_call')}
    for item in checked:
        counts[label(item.verdict)] += 1
    scores = [c.verdict.freshness for c in checked if c.verdict.freshness is not None]
    result = CheckResult(run_id=run.id,answer=req.answer,freshness=round(sum(scores)/len(scores)) if scores else None,
                         counts=counts,claims=checked,corrected=correction,telemetry=run.finish())
    return {'checked':checked,'result':result}

async def persist(state):
    result,run = state['result'],state['run']
    if run.budget_exhausted:
        raise BudgetExhausted()
    await store.set('run:'+run.id,result.model_dump(mode='json'),604800)
    await run.emit('run.telemetry',telemetry=result.telemetry)
    await run.emit('run.completed',result=result)
    return {}

builder = StateGraph(State)
builder.add_node('Stepover',plan)
builder.add_node('parallel_VAR',investigate)
builder.add_node('persist',persist)
builder.add_edge(START,'Stepover')
builder.add_edge('Stepover','parallel_VAR')
builder.add_edge('parallel_VAR','persist')
builder.add_edge('persist',END)
graph = builder.compile()

async def check(request,run):
    run.telemetry.tavily.live = request.live
    await run.emit('run.started',mode='check')
    result = await graph.ainvoke({'request':request,'run':run})
    return result['result']
