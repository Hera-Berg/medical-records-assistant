"""Authenticated, per-clinician summary adapters; no consultation/review completion writes."""
from datetime import date
from fastapi import HTTPException
from ..summary import store
from .clinician_dashboard import build


def context_for(state, account, selected):
    data=build(state,account,selected)
    period=data['comparison_period']
    if not period['start_date']:
        raise HTTPException(409,'First review of this patient. Select a comparison date before preparing a changes summary.')
    return {'clinician':{key:account[key] for key in ('username','name','role','salutation')},
            'period':period,'groups':data['groups'],
            'basis':('Clinician comparison for '+account['name']+': '+
                     ('selected custom period' if period['mode']=='custom' else 'own last completed consultation')+
                     ' · Australia/Sydney. Prepared demo summaries require clinician review.')}


def reference(context):
    p=context['period']
    return store.Reference(ts=p['start_timestamp'],day=date.fromisoformat(p['start_date']),
                           source='clinician',note_override=context['basis'])


def authorize_summary(request, summary_id, state):
    event=store.find(state.snapshot().events,summary_id)
    context=event.payload.get('clinician_context') if event else None
    if context:
        from ..clinician_auth import COOKIE
        session=request.app.state.clinician_auth.resolve(request.cookies.get(COOKIE))
        if not session:
            raise HTTPException(401,'Sign in to read this clinician summary.')
        if context['clinician']['username']!=session['account']['username']:
            raise HTTPException(403,'This clinician summary belongs to another account.')
    return event
