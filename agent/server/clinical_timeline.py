"""One clinician timeline entry per original source, never per extracted claim.

Sealed clinical notes, workflow audit events and condition assertions are not
inputs. Their contents cannot become previews through this projection.
"""
from datetime import datetime, timedelta
from .comparison import SYDNEY, day, instant
from .review_workflow import sources
from ..demo_metadata import entries

CATEGORIES=('Letters','Test results','Patient & carer updates','Nursing/GP notes','Medication changes')


def build(state, clinician=True):
    snapshot=state.snapshot()
    records=sources(snapshot)
    # Retain older authored sources where present; do not recreate extracted facts.
    for key,event in entries(snapshot.events,snapshot.artifacts).items():
        records.setdefault(key,event.payload)
    result=[];seen=set()
    for key,p in records.items():
        artifact=p.get('artifact')
        identity=artifact or key
        if identity in seen:continue
        seen.add(identity)
        if key=='01M62NQPN2MQ6VB8DJ9ZJHVTEP' and p.get('text','').strip().casefold()=='why i':continue
        native=p.get('native_document') or {}
        role=p.get('role') or 'Role not recorded'
        author=p.get('author') or 'Author not recorded'
        title=native.get('ordered_panel') or p.get('title') or 'Uploaded document'
        original=p.get('text') or ''
        received=native.get('received_date')
        document=native.get('date') or p.get('document_date')
        event=p.get('event_timestamp') or p.get('observation_timestamp') or p.get('observation_date') or p.get('event_date')
        recorded=p.get('recorded_at')
        category='Other documents';kind='Document';basis='Document date'
        date=document or event
        if native.get('results') or role.lower() in ('laboratory','pathologist'):
            category='Test results';kind='Test result';date=event or document;basis='Event / collection date' if event else 'Document date'
        elif p.get('source')=='typed' or (p.get('access')=='submission' and not any(v in role.lower() for v in ('nurse','gp','doctor'))):
            category='Patient & carer updates'
            kind='Patient update' if role.lower()=='patient' else 'Carer update' if any(v in role.lower() for v in ('carer','daughter','son')) else 'Submitted update'
            date=event or recorded;basis='Observation date' if not p.get('observation_unknown') and event else 'Recorded date · Observation date not supplied'
        elif key in ('gp-af','med-chart') or native.get('prescriptions') or p.get('event_type')=='medication':
            # Only explicitly medication-labelled original sources, never problem entities.
            category='Medication changes';kind='Medication chart' if key=='med-chart' else 'Prescription record' if key=='gp-af' or native.get('prescriptions') else 'Medication record';date=event or document;basis='Source event date'
        elif any(v in role.lower() for v in ('nurse','gp','general practitioner')):
            category='Nursing/GP notes';kind='Nursing note' if 'nurse' in role.lower() else 'GP note';date=event or document;basis='Note date'
        elif p.get('recommendation') or any(v in role.lower() for v in ('cardiologist','physiotherapist','audiologist','podiatrist','specialist')) or p.get('event_type')=='letter':
            category='Letters';kind='Letter received' if received else 'Letter';date=received or document or event;basis='Received date' if received else 'Document date · Receipt date not recorded'
        appointment=native.get('calendar_appointment') or {}
        if p.get('consultation') and appointment.get('status')=='Completed' and appointment.get('time') and day(event):
            try:
                date=datetime.fromisoformat(day(event).isoformat()+'T'+appointment['time']).replace(tzinfo=SYDNEY).isoformat()
                basis='Completed consultation time'
            except ValueError:pass
        if p.get('upload_only'):
            item=snapshot.artifacts.get(artifact)
            date=item.ingested_ts if item else None;basis='Uploaded date · Document and receipt dates not recorded'
            kind='Document uploaded'
        parsed=instant(date)
        dated=day(date)
        if parsed:
            sort=parsed.timestamp();date_value=parsed.astimezone(SYDNEY).isoformat()
        elif dated:
            sort=datetime.combine(dated,datetime.min.time(),SYDNEY).timestamp();date_value=dated.isoformat()
        else:sort=float('-inf');date_value=None
        is_note=category in ('Patient & carer updates','Nursing/GP notes')
        prefix='/clinician' if clinician else ''
        source_url=prefix+'/artifact/'+artifact if artifact else prefix+'/observation/'+key
        is_pdf=bool(artifact and snapshot.artifacts[artifact].mime=='application/pdf')
        result.append(dict(id=identity,category=category,event_type=kind,date=date_value,month=dated.strftime('%Y-%m') if dated else None,
            date_basis=basis,document_date=document,received_date=received,author=author,role=role,
            organisation=native.get('organisation') or p.get('organisation'),title=title,
            excerpt=original[:300] if is_note else None,excerpt_truncated=is_note and len(original)>300,
            source_url=source_url,original_url=('/api/clinician' if clinician else '/api')+'/artifact/'+artifact if is_pdf else source_url,
            link_label='View full note' if category=='Nursing/GP notes' else 'View original note' if is_note else 'Open PDF' if is_pdf else 'Open original',_sort=sort))
    result.sort(key=lambda r:(r['_sort'],r['id']),reverse=True)
    for row in result:row.pop('_sort')
    today=state.now().astimezone(SYDNEY).date()
    return dict(events=result,categories=list(CATEGORIES)+(['Other documents'] if any(r['category']=='Other documents' for r in result) else []),
                today=today.isoformat(),default_from=(today-timedelta(days=29)).isoformat(),timezone='Australia/Sydney',
                limitations='Missing author, role, practice and receipt dates are left unknown. '+('Encrypted Progress notes remain in their protected page; they are not previewed here.' if clinician else 'This shared patient/carer demo shows your submissions and documents made available to you. Unreleased clinical documents and private Progress notes are excluded.'))
