"""Server-verified clinician sessions and the read-only Jane review dashboard."""
import hmac
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from ...clinician_auth import COOKIE, MAX_SECONDS
from ..deps import get_state
from ..state import RecordState

router = APIRouter()


def same_origin(request):
    origin = request.headers.get('origin')
    if origin and (urlsplit(origin).netloc != request.headers.get('host')
                   or urlsplit(origin).scheme != request.url.scheme):
        raise HTTPException(403, 'Use the sign-in form on this application.')
    if request.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none'):
        raise HTTPException(403, 'Cross-origin account requests are refused.')


@router.post('/api/auth/clinician/login')
async def login(request: Request):
    same_origin(request)
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise HTTPException(415, 'Send the sign-in form as JSON.')
    if len(await request.body()) > 4096:
        raise HTTPException(400, 'Check the username and password.')
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, 'Check the username and password.') from None
    if (not isinstance(body, dict) or set(body) != {'username','password'}
            or any(not isinstance(v,str) for v in body.values())
            or len(body['username'])>128 or len(body['password'])>1024):
        raise HTTPException(400, 'Check the username and password.')
    store = request.app.state.clinician_auth
    result, error = await run_in_threadpool(store.login, body['username'].strip(), body['password'],
                                request.client.host if request.client else 'local', request.cookies.get(COOKIE))
    if error:
        raise HTTPException(429 if error=='limited' else 401,
            'Too many attempts. Try again in 15 minutes.' if error=='limited' else 'Incorrect username or password.')
    token, session = result
    response = JSONResponse({'clinician':store.public(session['account']), 'csrf':session['csrf']})
    response.set_cookie(COOKIE, token, httponly=True, secure=request.url.scheme=='https',
                        samesite='strict', max_age=MAX_SECONDS, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/api/auth/clinician/session')
def current(request: Request):
    store = request.app.state.clinician_auth
    session = store.resolve(request.cookies.get(COOKIE), touch=False)
    if not session:
        raise HTTPException(401, 'Sign in to the clinician workspace.')
    return {'clinician':store.public(session['account']), 'csrf':session['csrf']}


@router.post('/api/auth/clinician/logout')
def logout(request: Request):
    same_origin(request)
    store = request.app.state.clinician_auth
    session = store.resolve(request.cookies.get(COOKIE))
    if session and not hmac.compare_digest(request.headers.get('x-clinician-csrf',''), session['csrf']):
        raise HTTPException(403, 'Refresh this page before signing out.')
    store.revoke(request.cookies.get(COOKIE))
    response = JSONResponse({'signed_out':True})
    response.delete_cookie(COOKIE, path='/', httponly=True, samesite='strict', secure=request.url.scheme=='https')
    response.headers['Clear-Site-Data'] = '"cache"'
    return response


@router.get('/api/clinician/dashboard')
def dashboard(request: Request, comparison_date: str | None = None, state: RecordState=Depends(get_state)):
    from ..clinician_dashboard import build
    return build(state, request.state.clinician, comparison_date)
