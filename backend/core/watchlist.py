import hashlib
import hmac
import os
import re
import secrets
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from .http import guard

router = APIRouter(prefix='/api/py/watchlist')
COOKIE = 'offside_watch'


class BudgetExhausted(Exception):
    pass


def internal_request(request):
    supplied = request.headers.get('x-pathway-token')
    if supplied is None:
        return False
    expected = os.getenv('PATHWAY_TOKEN', '')
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, 'Invalid monitoring credentials.')
    return True


async def reserve_credits(credits):
    try:
        result = await proxy('POST', '/budget', {'credits': credits})
        if not result.get('allowed'):
            raise BudgetExhausted()
    except Exception as exc:
        raise BudgetExhausted() from exc


async def proxy(method, path, body=None):
    url, token = os.getenv('PATHWAY_URL'), os.getenv('PATHWAY_TOKEN')
    if not url or not token:
        raise HTTPException(503, 'Monitoring is not configured yet.')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, url.rstrip('/')+path, json=body,
                                            headers={'Authorization': 'Bearer '+token})
        if response.status_code in (404, 409, 422):
            raise HTTPException(response.status_code, response.json().get('detail', 'The watch could not be updated.'))
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        raise HTTPException(503, 'Monitoring is temporarily unavailable. Please try again shortly.')


def browser_owner(request):
    cookie = request.cookies.get(COOKIE, '')
    return cookie if re.fullmatch(r'[A-Za-z0-9_-]{43}', cookie) else None


def owner_hash(cookie):
    return hashlib.sha256(cookie.encode()).hexdigest()


def same_origin(request):
    origin = request.headers.get('origin')
    if origin and (urlsplit(origin).netloc != request.headers.get('host') or urlsplit(origin).scheme not in ('http','https')):
        raise HTTPException(403, 'Use this app to manage your watchlist.')


class CreateWatch(BaseModel):
    run_id: UUID


@router.get('')
async def watches(request: Request):
    owner = browser_owner(request)
    if not owner:
        return {'watches': [], 'available': bool(os.getenv('PATHWAY_URL') and os.getenv('PATHWAY_TOKEN'))}
    return await proxy('GET', '/watchlists/'+owner_hash(owner))


@router.post('', status_code=201)
async def create(body: CreateWatch, request: Request, response: Response):
    same_origin(request)
    await guard(request, 'offside-watchlist')
    owner = browser_owner(request) or secrets.token_urlsafe(32)
    result = await proxy('POST', '/watchlists/'+owner_hash(owner), {'run_id': str(body.run_id)})
    response.set_cookie(COOKIE, owner, max_age=604800, httponly=True, secure=bool(os.getenv('VERCEL')),
                        samesite='strict', path='/api/py/watchlist')
    return result


@router.delete('/{watch_id}')
async def stop(watch_id: UUID, request: Request):
    same_origin(request)
    owner = browser_owner(request)
    if not owner:
        raise HTTPException(404, 'This watch could not be found.')
    return await proxy('DELETE', '/watchlists/'+owner_hash(owner)+'/'+str(watch_id))
