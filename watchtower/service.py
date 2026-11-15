import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from .processor import Processor, source_urls
from .repository import WatchStore, connect, public_watch


def create_app(store, engine_health=lambda: True, lifespan=None):
    def authorize(authorization: str = Header(default='')):
        token = os.getenv('PATHWAY_TOKEN', '')
        if not token or not hmac.compare_digest(authorization, 'Bearer '+token):
            raise HTTPException(401, 'Authentication required.')

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get('/health')
    def health():
        try:
            ready = bool(store.redis.ping()) and engine_health()
        except Exception:
            ready = False
        if not ready:
            raise HTTPException(503, 'Monitoring is starting or temporarily unavailable.')
        return {'ok': True, 'engine': 'Pathway'}

    class CreateWatch(BaseModel):
        run_id: UUID

    class Credits(BaseModel):
        credits: int = Field(ge=1, le=10)

    def owner_key(owner):
        if len(owner) != 64 or any(c not in '0123456789abcdef' for c in owner):
            raise HTTPException(422, 'Invalid watchlist.')
        return owner

    @app.get('/watchlists/{owner}', dependencies=[Depends(authorize)])
    def watches(owner: str):
        return {'watches': [public_watch(w) for w in store.list(owner_key(owner))], 'available': True}

    @app.post('/watchlists/{owner}', dependencies=[Depends(authorize)], status_code=201)
    def create(owner: str, body: CreateWatch):
        owner_key(owner)
        raw = store.redis.get('offside:run:'+str(body.run_id))
        if not raw:
            raise HTTPException(404, 'This result expired. Run a fresh check first.')
        result = json.loads(raw)
        urls = source_urls(result)
        if not urls:
            raise HTTPException(422, 'This answer has no readable public sources to watch.')
        try:
            return public_watch(store.create(owner, str(body.run_id), result, urls))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.delete('/watchlists/{owner}/{watch_id}', dependencies=[Depends(authorize)])
    def stop(owner: str, watch_id: UUID):
        if not store.stop(owner_key(owner), str(watch_id)):
            raise HTTPException(404, 'This watch could not be found.')
        return {'stopped': True}

    @app.post('/budget', dependencies=[Depends(authorize)])
    def budget(body: Credits):
        return {'allowed': store.reserve(body.credits)}

    return app


def run_engine(run, stop_event):
    try:
        run()
    except Exception:
        logging.error("Pathway engine stopped unexpectedly; restarting service", exc_info=False)
    finally:
        if not stop_event.is_set():
            # Let Railway restart the process; Redis leases retain unfinished work.
            os._exit(1)


def main():
    import pathway as pw
    import uvicorn
    from .pipeline import WatchSubject, build_pipeline
    store = WatchStore(connect())
    processor = Processor(store)
    subject = WatchSubject(processor)
    build_pipeline(subject, processor.process)
    engine = threading.Thread(target=run_engine, args=(lambda: pw.run(monitoring_level=pw.MonitoringLevel.NONE), subject.stop_event), daemon=True)

    @asynccontextmanager
    async def lifespan(app):
        engine.start()
        yield
        subject.stop_event.set()

    app = create_app(store, lambda: engine.is_alive() and time.monotonic()-subject.heartbeat < 240, lifespan)
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')), access_log=False)


if __name__ == '__main__':
    main()
