"""Recipient-labelled shared demo projection; no individual patient authentication."""
from dataclasses import replace
from ..projection import project
from ..demo_metadata import history as source_history
from ..projection.citations import Citer, index_artifacts
from .review_workflow import enabled, sources, history

class NoPrivateIndex:
    def ensure(self,*args):return False
    def discard(self):pass

class PatientState:
    def __init__(self,state):self._state=state
    def __getattr__(self,name):return getattr(self._state,name)
    def snapshot(self):
        snapshot=self._state.snapshot()
        source=sources(snapshot)
        released={a['artifact'] for a in history(snapshot.events) if a['kind']=='release'}
        protected={p['artifact'] for p in source.values() if p['access']=='clinical' and p.get('artifact')}
        protected.update(e.payload['artifact'] for e in source_history(snapshot.events,snapshot.artifacts) if e.payload.get('access')=='clinical')
        hidden=protected-released
        # Related originals/claims are filtered before any public projection or citation.
        rejected_ids={e.id for e in snapshot.events if (e.provenance or {}).get('artifact') in hidden or e.payload.get('artifact') in hidden or e.payload.get('short') in hidden or e.payload.get('review_workflow') or e.type.startswith('compartment.') or e.type.startswith('clinician.note.') or (e.type=='summary.generated' and e.payload.get('clinician_context')) or e.payload.get('clinical_upload') or e.payload.get('submission_workspace')=='clinician'}
        filtered=[]
        for event in snapshot.events:
            p=event.payload
            workflow=p.get('review_workflow') or {}
            if workflow.get('kind')=='release':
                visible={k:workflow[k] for k in ('kind','artifact','actor','recipients','message')}
                filtered.append(replace(event,payload={'document_release':visible}))
                continue
            if event.id in rejected_ids or p.get('target') in rejected_ids:continue
            filtered.append(event)
        events=tuple(filtered);artifacts=index_artifacts(events)
        return replace(snapshot,events=events,projection=project(events,snapshot.as_of),artifacts=artifacts,citer=Citer(artifacts,events),fingerprint=snapshot.fingerprint+(('patient-shared-view',len(events)),))

def state_for(request):
    state=request.app.state.record
    if not request.url.path.startswith('/api/clinician/') and not request.url.path.startswith('/clinician/') and not request.url.path.startswith('/api/doctor-notes') and not (request.url.path.startswith('/api/review') and getattr(request.state,'clinician',None)) and enabled(state.snapshot().events):return PatientState(state)
    return state
