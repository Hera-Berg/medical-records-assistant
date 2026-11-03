"""Shared patient/carer demo notifications, fictional bookings and document catalogue."""
from datetime import datetime,timedelta,time
import hashlib
from fastapi import HTTPException
from ..events import envelope
from ..demo_metadata import entries
from .comparison import SYDNEY
from .review_workflow import enabled,history,public_feed,RECIPIENTS
from .routes.calendar import calendar_events

CATALOGUE=('critical-sodium','low-haemoglobin','stable-renal','cardio-recommendation','med-chart','podiatry-correspondence','audiology-correspondence','previous-pathology','physio','smith-consult','nurse-clinical','baseline')

def bookings(events):
    return [dict(e.payload['patient_booking'],event=e.id,recorded_at=e.ts) for e in events if isinstance(e.payload.get('patient_booking'),dict)]

def availability(state):
    snapshot=state.snapshot();now=state.now().astimezone(SYDNEY)
    if not enabled(snapshot.events):return {'slots':[],'timezone':'Australia/Sydney','fictional':True}
    calendar=calendar_events(snapshot,now)
    occupied=[e for e in calendar if e.get('kind')=='appointment' and e.get('status') not in ('Cancelled','Canceled')]
    location=next((e.get('location') for e in calendar if e.get('professional')=='Dr James Smith' and e.get('location')),None)
    slots=[]
    for offset in range(1,15):
        day=now.date()+timedelta(days=offset)
        if day.weekday()>4:continue
        for hour,minute in ((9,30),(11,0),(14,0)):
            wall=datetime.combine(day,time(hour,minute),tzinfo=SYDNEY)
            def clashes(e):
                if e['date']!=day.isoformat():return False
                # Un-timed existing appointments cannot establish a free slot on that day.
                if not e.get('time'):return True
                start=datetime.fromisoformat(e['date']+'T'+e['time']).replace(tzinfo=SYDNEY)
                return start<wall+timedelta(minutes=30) and wall<start+timedelta(minutes=e.get('duration_minutes',30))
            # Single-patient demo: do not offer an overlapping appointment for Jane or Dr Smith.
            if any(clashes(e) for e in occupied):continue
            slots.append(dict(id='smith:'+day.isoformat()+':'+wall.strftime('%H%M'),date=day.isoformat(),time=wall.strftime('%H:%M'),
                              starts_at=wall.isoformat(),professional='Dr James Smith',location=location,duration_minutes=30,fictional=True))
            if len(slots)==12:return {'slots':slots,'timezone':'Australia/Sydney','fictional':True}
    return {'slots':slots,'timezone':'Australia/Sydney','fictional':True}

def book(state,body):
    if not enabled(state.snapshot().events):raise HTTPException(409,'Fictional appointment booking is available in Jane’s demonstration record.')
    with state.lock:
        feed=public_feed(state)
        message=next((m for m in feed['messages'] if m['id']==body.get('request') and m['kind']=='appointment_request'),None)
        if not message:raise HTTPException(404,'Appointment request not found.')
        recipient=body.get('recipient')
        if recipient not in message['recipients']:raise HTTPException(400,'Select the patient or an authorised recipient of this request.')
        if body.get('confirmed') is not True:raise HTTPException(400,'Preview and confirm the appointment before booking.')
        token=body.get('request_id')
        if not isinstance(token,str) or not token or len(token)>100:raise HTTPException(400,'Refresh the booking form and try again.')
        existing=next((b for b in bookings(state.snapshot().events) if b['request']==message['id']),None)
        if existing:
            if existing['slot_id']!=body.get('slot'):raise HTTPException(409,'This request already has a booking. View it in the calendar.')
            return {'booking':existing,'duplicate':True}
        if message.get('action_resolved'):raise HTTPException(409,'This appointment request has been closed by the clinician.')
        slot=next((s for s in availability(state)['slots'] if s['id']==body.get('slot')),None)
        if not slot:raise HTTPException(409,'That time is no longer available. Choose another time.')
        identifier='booking:'+envelope.ulid.new()
        row=dict(id=identifier,request=message['id'],slot_id=slot['id'],date=slot['date'],time=slot['time'],professional=slot['professional'],location=slot['location'],
                 recipient=recipient,booked_by=RECIPIENTS[recipient],simulation=True,request_id=token)
        calendar=dict(date=slot['date'],time=slot['time'],title='GP appointment to discuss your report',professional=slot['professional'],location=slot['location'],
                      duration_minutes=30,kind='appointment',status='Booked',description=message['message'])
        # One event makes the booking and calendar entry durable together.
        event=envelope.new('note.recorded',state.vault.identity.id,ts=envelope.format_ts(state.now()),payload={'patient_booking':row,'calendar_only':True,'calendar_id':identifier,'calendar_entry':calendar,'fictional':True})
        state.append(event)
        return {'booking':dict(row,event=event.id,recorded_at=event.ts),'duplicate':False}

def documents(state,clinician=False):
    snapshot=state.snapshot();sources=entries(snapshot.events,snapshot.artifacts)
    released={a['artifact']:a for a in history(snapshot.events) if a['kind']=='release'}
    selected=[sources[id] for id in CATALOGUE if id in sources]
    selected_ids={e.payload['artifact'] for e in selected}
    # Remaining real uploads stay reachable through View all, never silently discarded.
    other=[e for e in sources.values() if e.payload['artifact'] not in selected_ids and e.payload.get('access')!='submission']
    all_source_artifacts={e.payload.get('artifact') for e in snapshot.events if e.payload.get('demo_item_id')}
    rows=[]
    for e in selected+other:
        p=e.payload;native=p.get('native_document') or {};short=p['artifact'];release=released.get(short)
        accessible=clinician or p.get('access') in (None,'submission') or bool(release)
        title=native.get('ordered_panel') or p['title']
        if p.get('role')=='Cardiologist':title=p['author']+' – Cardiologist letter'
        rows.append(dict(id='document:'+hashlib.sha256(p['demo_item_id'].encode()).hexdigest()[:16],title=title,date=native.get('date') or p['event_date'],received_date=native.get('received_date'),
                         author=p['author'],category='Pathology' if native.get('results') else 'Medical document',
                         accessible=accessible,url=('/clinician/artifact/' if clinician else '/artifact/')+short if accessible else None,
                         status='Available' if accessible else 'Awaiting clinician release',release_message=release.get('message') if release else None))
    # All ordinary patient/carer and external uploads share the same list; no example/upload collections.
    clinical_uploads={e.payload['clinical_upload'] for e in snapshot.events if e.payload.get('clinical_upload')}
    names={e.payload.get('short'):(e.payload.get('capture') or {}).get('original_filename') for e in snapshot.events if e.type=='artifact.ingested'}
    for short,a in sorted(snapshot.artifacts.items(),key=lambda item:item[1].ingested_ts or '',reverse=True):
        if short in all_source_artifacts:continue
        accessible=clinician or short not in clinical_uploads or short in released
        rows.append(dict(id='upload:'+short,title=names.get(short) or 'Uploaded document',date=None,received_date=None,author='Contributor not recorded',category='Uploaded document',accessible=accessible,
                         url=('/clinician/artifact/' if clinician else '/artifact/')+short if accessible else None,status='Available' if accessible else 'Awaiting clinician release'))
    rows=[r for r in rows if r['id'].startswith('upload:')]+[r for r in rows if not r['id'].startswith('upload:')]
    if clinician:
        from types import SimpleNamespace
        from .review_workflow import sources as workflow_sources, issue_key, states, contribution
        review_sources=workflow_sources(snapshot)
        by_artifact={p['artifact']:p for p in review_sources.values() if p.get('artifact')}
        for row in rows:
            short=(row.get('url') or '').rsplit('/',1)[-1]
            p=by_artifact.get(short)
            if not p or p.get('access')!='clinical':continue
            key=issue_key(p)
            state_info=states(snapshot.events,key,[p['demo_item_id']])
            row['released']=any(a.get('source')==p['demo_item_id'] for a in state_info['releases'])
            row['workflow_issue']=dict(id=key,headline=row['title'],contributions=[contribution(p,snapshot,SimpleNamespace(position=lambda value:None))],workflow=state_info,priority=state_info['priority']['priority'] if state_info['priority'] else 'untriaged')
    return {'documents':rows,'total':len(rows),'initial_limit':10,'fictional':True,'access_note':'Unreleased clinician documents are listed by name only. Their contents remain protected.'}


def submitted_notes(state):
    events=state.snapshot().events
    corrected={e.payload['correction_for']:e for e in events if e.payload.get('correction_for') and isinstance(e.payload.get('corrected_text'),str)}
    notes=[]
    for event in events:
        p=event.payload;key=None
        if event.type!='note.recorded':continue
        if p.get('source')=='typed' and not p.get('correction_for') and p.get('submission_workspace')!='clinician' and not p.get('demo_scenario'):
            key=event.id;author=p.get('author') or 'Author not recorded';text=p.get('text')
        elif p.get('access')=='submission' and (p.get('role')=='Patient' or 'carer' in p.get('role','').lower()):
            key=p['demo_item_id'];author=p['author'];text=p.get('text')
        if not key or not isinstance(text,str):continue
        correction=corrected.get(key)
        notes.append(dict(id=key,text=correction.payload['corrected_text'] if correction else text,original_text=text,author=author,
                          recorded_at=event.ts,event_date=p.get('observation_date') or p.get('event_date'),revision=correction.id if correction else event.id,
                          corrected_at=correction.ts if correction else None,can_correct=True,simulation=True))
    return sorted(notes,key=lambda n:(n['corrected_at'] or n['recorded_at'],n['id']),reverse=True)


def correct_note(state,identifier,body):
    with state.lock:
        note=next((n for n in submitted_notes(state) if n['id']==identifier),None)
        if not note:raise HTTPException(403,'Only shared patient/carer submitted notes can receive a correction here.')
        text=body.get('text')
        if not isinstance(text,str) or not text.strip() or len(text.strip())>10000:raise HTTPException(400,'Enter a correction of at most 10,000 characters.')
        text=text.strip()
        if note['text']==text:return {'duplicate':True}
        if body.get('revision')!=note['revision']:raise HTTPException(409,'This note has changed. Reload before adding your correction.')
        date=note['event_date'] or note['recorded_at'][:10]
        description=f'Correction to a shared patient/carer update dated {date}. Contributor identity is not authenticated in this shared demo.\n\n'+text
        event=envelope.new('note.recorded',state.vault.identity.id,ts=envelope.format_ts(state.now()),payload={'source':'typed','submission_workspace':'patient-carer',
            'text':description,'correction_for':identifier,'corrected_text':text,'corrects_revision':note['revision']})
        state.append(event)
        return {'event':event.id,'duplicate':False}
