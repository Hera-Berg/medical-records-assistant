"""Server-verified clinician sessions and the read-only Jane review dashboard."""
import hmac
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Body
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


@router.get('/api/clinician/observations/{event_id}')
def original_observation(event_id: str, state: RecordState=Depends(get_state)):
    for event in state.snapshot().events:
        p = event.payload
        if event.id == event_id and event.type == 'note.recorded' and p.get('source') == 'typed' and not p.get('demo_scenario') and isinstance(p.get('text'), str):
            return {'id':event.id, 'text':p['text'], 'recorded_at':event.ts,
                    'author':p.get('author') or 'Author not recorded', 'role':p.get('role') or 'Typed record contribution',
                    'date_basis':('Observation date: '+p['observation_date']) if p.get('observation_date') else 'Recorded date · Observation date not supplied'}
    raise HTTPException(404, 'This original typed observation was not found.')


@router.get('/api/clinician/summary')
def clinician_summaries(request: Request,state: RecordState=Depends(get_state)):
    from ...summary import store
    snapshot=state.snapshot()
    own={e.id for e in snapshot.events if e.payload.get('clinician_context',{}).get('clinician',{}).get('username')==request.state.clinician['username']}
    return {'summaries':[row for row in store.listing(snapshot.events) if row['id'] in own],'as_of':snapshot.built_ts}


def _clinical_summary_payload(summary, context):
    from ...summary import markdown
    data=summary.to_dict()
    data.update({'comparison_period':context['period'],'comparison_basis':context['basis'],
                 'markdown':markdown.render(summary).decode(), 'print_url':'/clinician/print/'+summary.id})
    return data


@router.post('/api/clinician/summary/preview')
def clinician_preview(request: Request, body:dict=Body(default_factory=dict),state:RecordState=Depends(get_state)):
    from ..clinician_summary import context_for, reference
    from ...summary import store
    context=context_for(state,request.state.clinician,body.get('since'))
    snapshot=state.snapshot()
    summary=store.compose(snapshot.events,as_of=snapshot.as_of,question=str(body.get('question') or ''),
                          label=str(body.get('label') or ''),reference=reference(context),demo=state.vault.is_demo,
                          clinician_context=context)
    return _clinical_summary_payload(summary,context)


@router.post('/api/clinician/summary')
def clinician_prepare(request: Request,body:dict=Body(default_factory=dict),state:RecordState=Depends(get_state)):
    from ..clinician_summary import context_for
    from ...summary import store
    with state.lock:
        context=context_for(state,request.state.clinician,body.get('since'))
        snapshot=state.snapshot()
        result=store.prepare(state.vault,as_of=snapshot.as_of,question=str(body.get('question') or ''),
                             label=str(body.get('label') or ''),events=snapshot.events,append=state.append,
                             clinician_context=context)
    data=_clinical_summary_payload(result.summary,context)
    data.update({'event':result.event.id,'exports':{'markdown':result.markdown_path,'html':result.html_path}})
    return data


@router.get('/api/clinician/summary/{summary_id}')
def clinician_one_summary(summary_id:str,request:Request,state:RecordState=Depends(get_state)):
    from ..clinician_summary import authorize_summary
    from ...summary import store
    event=authorize_summary(request,summary_id,state)
    if not event or not event.payload.get('clinician_context'):
        raise HTTPException(404,'No clinician summary with this ID.')
    restored=store.restore(state.snapshot().events,summary_id)
    if restored.summary is None:
        return {'id':summary_id,'summary':None,'withdrawn':len(restored.withdrawn),'message':restored.explanation}
    data=_clinical_summary_payload(restored.summary,event.payload['clinician_context'])
    data.update({'withdrawn':0,'exports':restored.exports})
    return data


@router.get('/clinician/print/{summary_id}')
def clinician_print(summary_id:str,request:Request,state:RecordState=Depends(get_state)):
    from .summary import print_sheet
    return print_sheet(summary_id,request,state)


@router.post('/api/clinician/issues/{issue_id}/workflow')
def review_action(issue_id:str,request:Request,body:dict=Body(...),state:RecordState=Depends(get_state)):
    from ..review_workflow import mutate
    return mutate(state,request.state.clinician,issue_id,body)


@router.get('/api/shared-care')
def shared_care(request:Request):
    from ..review_workflow import public_feed
    return public_feed(request.app.state.record)


@router.post('/api/shared-care/acknowledge')
def acknowledge(request:Request,body:dict=Body(...)):
    from ..review_workflow import public_feed,RECIPIENTS
    from ...events import envelope
    same_origin(request)
    state=request.app.state.record
    with state.lock:
        feed=public_feed(state)
        message=next((m for m in feed['messages'] if m['id']==body.get('message')),None)
        recipient=body.get('recipient')
        if not message or recipient not in message['recipients']:raise HTTPException(400,'Select a recipient for this demo acknowledgement.')
        if any(a['recipient']==recipient for a in message['acknowledgements']):return {'duplicate':True}
        state.append(envelope.new('note.recorded',state.vault.identity.id,ts=envelope.format_ts(state.now()),payload={'review_acknowledgement':{'message':message['id'],'recipient':recipient,'recipient_name':RECIPIENTS[recipient],'simulation':True}}))
    return {'acknowledged':True,'simulation':True}


@router.get('/api/patient/appointments/availability')
def patient_availability(request:Request):
    from ..patient_portal import availability
    return availability(request.app.state.record)


@router.post('/api/patient/appointments/book')
def patient_booking(request:Request,body:dict=Body(...)):
    from ..patient_portal import book
    same_origin(request)
    return book(request.app.state.record,body)


@router.get('/api/patient/documents')
def patient_documents(request:Request):
    from ..patient_portal import documents
    return documents(request.app.state.record)


@router.get('/api/clinician/document-list')
def clinician_document_list(request:Request):
    from ..patient_portal import documents
    return documents(request.app.state.record,clinician=True)


@router.get('/api/patient/updates')
def patient_updates(request:Request):
    from ..patient_portal import submitted_notes
    return {'notes':submitted_notes(request.app.state.record),'simulation':'Shared patient/carer workspace; individual authorship is not authenticated. Corrections retain the original.'}


@router.post('/api/patient/updates/{identifier}/correct')
def patient_correction(identifier:str,request:Request,body:dict=Body(...)):
    from ..patient_portal import correct_note
    same_origin(request)
    return correct_note(request.app.state.record,identifier,body)


@router.get('/api/clinician/progress-notes')
def progress_notes(state:RecordState=Depends(get_state)):
    from ...progress_notes import listing
    try:return listing(state)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None


@router.post('/api/clinician/progress-notes')
def add_progress_note(request:Request,body:dict=Body(...),state:RecordState=Depends(get_state)):
    from ...progress_notes import add
    try:return add(state,request.state.clinician,body)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.post('/api/clinician/progress-notes/import-legacy')
def import_progress_notes(body:dict=Body(...),state:RecordState=Depends(get_state)):
    from ...progress_notes import import_legacy
    try:return import_legacy(state,body.get('passphrase'))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get('/api/clinician/timeline/feed')
def timeline_feed(request:Request,state:RecordState=Depends(get_state)):
    from ..review_workflow import dashboard
    from ..clinical_timeline import build
    dashboard(state,request.state.clinician)  # Existing patient-assignment check.
    return build(state)


@router.get('/api/patient/timeline/feed')
def patient_timeline_feed(request:Request):
    from ..patient_access import PatientState
    from ..clinical_timeline import build
    return build(PatientState(request.app.state.record),clinician=False)


@router.get('/api/patient/observations/{event_id}')
def patient_original_observation(event_id:str,request:Request):
    from ..patient_access import PatientState
    return original_observation(event_id,PatientState(request.app.state.record))


@router.get('/api/clinician/clinical-record')
def clinical_record(request:Request,state:RecordState=Depends(get_state)):
    from ..clinical_record import build
    from ..review_workflow import dashboard
    dashboard(state,request.state.clinician)
    return build(state)


@router.post('/api/clinician/clinical-record')
def update_clinical_record(request:Request,body:dict=Body(...),state:RecordState=Depends(get_state)):
    from ..clinical_record import mutate
    return mutate(state,request.state.clinician,body)
