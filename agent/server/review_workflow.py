"""Append-only review/communication/release workflow for the assigned Jane record."""
import hashlib
import json
from datetime import datetime
from fastapi import HTTPException
from ..events import envelope
from ..demo_metadata import entries, source_ids
from .comparison import resolve, day, instant, SYDNEY
from .routes.calendar import calendar_events

VERSION='jane-review-v2'
KINDS={'review','priority','communication','appointment_request','release','action_resolved','action_created'}
RECIPIENTS={'patient':'Jane Doe','carer':'Emily Doe'}

def enabled(events):
    return any(e.payload.get('review_dataset')==VERSION for e in events)

def sources(snapshot):
    result={e.payload['demo_item_id']:e.payload for e in entries(snapshot.events,snapshot.artifacts).values() if e.payload.get('review_dataset')==VERSION}
    known=source_ids(snapshot.events,snapshot.artifacts)
    private={e.payload['clinical_upload']:e for e in snapshot.events if e.payload.get('clinical_upload')}
    for event in snapshot.events:
        p=event.payload
        if event.type=='note.recorded' and p.get('source')=='typed' and not p.get('demo_scenario') and isinstance(p.get('text'),str):
            clinical=p.get('observation_timestamp') or p.get('observation_date')
            result[event.id]=dict(demo_item_id=event.id,title='Correction to a submitted update' if p.get('correction_for') else 'Typed observation recorded',text=p['text'],author=p.get('author') or 'Author not recorded',role=p.get('role') or 'Typed record contributor',
                event_date=day(clinical or event.ts).isoformat(),event_timestamp=clinical if instant(clinical) else None,recorded_at=event.ts,
                observation_unknown=not clinical,access='submission',artifact=None,native_document={})
    submissions={e.payload["submission_artifact"]:e.payload for e in snapshot.events if e.payload.get("submission_artifact")}
    for short,artifact in snapshot.artifacts.items():
        if short in known:continue
        marked=private.get(short)
        submission=submissions.get(short,{})
        result['upload:'+short]=dict(demo_item_id='upload:'+short,title='Document uploaded to the record',text='Open the original document for its full contents. No clinical interpretation has been generated.',
            author=marked.payload.get('author','Author not recorded') if marked else submission.get('author','Author not recorded'),role=submission.get('role') or 'Document contributor',
            event_date=day(artifact.artifact_ts or artifact.ingested_ts).isoformat(),event_timestamp=None,access='clinical' if marked else 'submission',artifact=short,
            upload_only=True,native_document={'date':day(artifact.artifact_ts).isoformat() if day(artifact.artifact_ts) else None})
    return result

def history(events):
    return [dict(e.payload['review_workflow'],id=e.id,at=e.ts) for e in events if isinstance(e.payload.get('review_workflow'),dict)]

def retracted(events):
    return {a['target'] for a in history(events) if a['kind']=='notification_retracted'}


def retract_notification(state,identifier,basis):
    """User-authorised demo cleanup; retain the sent message and withdrawal in audit."""
    with state.lock:
        events=state.snapshot().events
        if identifier in retracted(events):return {'duplicate':True}
        target=next((a for a in history(events) if a['id']==identifier and (a['kind']=='appointment_request' or (a['kind']=='communication' and a.get('method')=='in_app'))),None)
        if not target:raise ValueError('Sent notification not found.')
        payload={'kind':'notification_retracted','issue':target['issue'],'target':identifier,'basis':basis,
                 'actor':{'username':'demo-maintenance','name':'Demo record correction','role':'User-requested administrative correction'}}
        event=envelope.new('note.recorded',state.vault.identity.id,payload={'review_workflow':payload})
        state.append(event);return {'event':event.id,'duplicate':False}


def issue_key(p):
    return {'nurse-clinical':'walking','meals':'dizziness'}.get(p['demo_item_id'],p.get('issue') or p['demo_item_id'])

def states(events, key, ids):
    audit=[a for a in history(events) if a.get('issue')==key]
    withdrawn=retracted(events)
    reviews=[a for a in audit if a['kind']=='review']
    # A prior review/release of the same source ID never approves newly added analytes.
    positions={event.id:n for n,event in enumerate(events)}
    revisions={event.payload['demo_item_id']:positions[event.id] for event in events if (event.payload.get('pathology_revision') or event.payload.get('document_revision')) and event.payload.get('demo_item_id')}
    reviewed={s for a in reviews for s in a['sources'] if positions[a['id']]>revisions.get(s,-1)}
    boundary=max((revisions.get(s,-1) for s in ids),default=-1)
    communications=[a for a in audit if a['kind'] in ('communication','appointment_request') and positions[a['id']]>boundary and a['id'] not in withdrawn]
    releases=[a for a in audit if a['kind']=='release' and positions[a['id']]>revisions.get(a.get('source'),-1)]
    resolved={a['action'] for a in audit if a['kind']=='action_resolved'}
    booked={e.payload['patient_booking']['request']:e.payload['patient_booking'] for e in events if isinstance(e.payload.get('patient_booking'),dict)}
    actions=[dict(a,resolved=a['id'] in resolved,booking=booked.get(a['id'])) for a in audit if a['kind'] in ('appointment_request','action_created') and a['id'] not in withdrawn]
    priorities=[a for a in audit if a['kind']=='priority']
    acknowledgements=[dict(e.payload['review_acknowledgement'],at=e.ts) for e in events if isinstance(e.payload.get('review_acknowledgement'),dict)]
    return dict(reviewed=bool(ids) and set(ids)<=reviewed,reviewed_sources=sorted(reviewed),reviews=reviews,
        communications=communications,releases=releases,actions=actions,priority=priorities[-1] if priorities else None,
        acknowledgements=[a for a in acknowledgements if a['message'] in {m['id'] for m in communications}],history=audit)

def contribution(p,snapshot,period,context=False):
    artifact=snapshot.artifacts.get(p['artifact'])
    native=p.get('native_document',{})
    position=period.position(p.get('event_timestamp') or p['event_date'])
    from ..document_titles import title as document_title
    return dict(id=p['demo_item_id'],headline=document_title(p),summary=p['text'],original_text=p['text'],author=p['author'],role=p['role'],
        date=p['event_date'],event_date=None if p.get('observation_unknown') or p.get('upload_only') else p['event_date'],recorded_at=p.get('recorded_at'),date_basis='Observation date not supplied' if p.get('observation_unknown') else None,document_date=native.get('date'),authored_date=native.get('authored_date'),
        received_date=native.get('received_date'),uploaded_at=artifact.ingested_ts if artifact else None,source_url='/clinician/artifact/'+p['artifact'] if p['artifact'] else '/clinician/observation/'+p['demo_item_id'],
        artifact=p['artifact'],ordered_panel=native.get('ordered_panel') or p.get('ordered_panel'),status='Prepared fictional source',review_status=None,results=native.get('results',[]),
        access=p['access'],laboratory_flag=p.get('laboratory_flag'),context=context,
        ordering='Same consultation day; ordering unknown' if position=='same-day-unknown' else None)

def dashboard(state,account,selected=None):
    snapshot=state.snapshot();now=state.now().astimezone(SYDNEY)
    from .. import profile,care_team
    identity=profile.read(snapshot.events)['values']
    if identity.get('full_name')!='Jane Doe' or not any(care_team.key(m['name'])==care_team.key(account['name']) for m in care_team.listing(snapshot.events,snapshot.artifacts,identity)):
        raise HTTPException(403,'This clinician is not assigned to this patient.')
    appointments=calendar_events(snapshot,now);period=resolve(appointments,account,now,selected)
    source=sources(snapshot);audit=history(snapshot.events);withdrawn=retracted(snapshot.events)
    unresolved_keys={a['issue'] for a in audit if a['kind'] in ('appointment_request','action_created') and a['id'] not in withdrawn and not any(h['kind']=='action_resolved' and h.get('action')==a['id'] for h in audit)}
    issues={};groups={k:[] for k in ('reported','documents','medications','follow_up','newly_received')}
    for id,p in source.items():
        clinical=p.get('event_timestamp') or (p.get('recorded_at') if p.get('observation_unknown') else None) or p['event_date'];received=p['native_document'].get('received_date')
        if p.get('upload_only'):received=snapshot.artifacts[p['artifact']].ingested_ts
        if day(clinical)>now.date() or (instant(clinical) and instant(clinical)>now):continue
        if period.last and p.get('artifact') and period.last.get('source_url')=='/artifact/'+p['artifact']:continue
        position=None if p.get('upload_only') else period.position(clinical)
        new_receipt=not position and period.position(received)
        key=issue_key(p)
        if not period.start_day or not (position or new_receipt or key in unresolved_keys):continue
        row=contribution(p,snapshot,period)
        row['outside_period']=not position and not new_receipt
        if new_receipt and period.position(received)=='same-day-unknown':row['ordering']='Receipt on consultation day; ordering unknown'
        row['label']='Uploaded document' if p.get('upload_only') else 'Newly received · historical document' if new_receipt else 'Recommendation documented' if p.get('recommendation') else 'Clinical document'
        group='reported' if p['access']=='submission' else 'newly_received' if new_receipt else 'documents'
        groups[group].append(row)
        issue=issues.setdefault(key,dict(id=key,headline={'walking':'Walking support requested','dizziness':'Persistent dizziness and meal participation reported','meals':'Jane reports fewer communal breakfasts','med-question':'Jane asks which heart tablet changed'}.get(key,p['title']),
            section='reported' if p['access']=='submission' else 'updates',label=row['label'] if p['access']!='submission' else None,summary=None,contributions=[],context=[],date=p['event_date'],sort_date=received if new_receipt else clinical,laboratory_flags=[]))
        issue['contributions'].append(row)
        issue['sort_date']=max(issue['sort_date'],received if new_receipt else clinical)
        if p.get('laboratory_flag'):issue['laboratory_flags'].append(p['laboratory_flag'])
    for issue in issues.values():
        if any(c['access']=='submission' for c in issue['contributions']):
            issue['section']='reported';issue['label']=None
        ids=[c['id'] for c in issue['contributions']]
        workflow=states(snapshot.events,issue['id'],ids);issue['workflow']=workflow
        priority=workflow['priority']
        issue['priority']=priority['priority'] if priority else 'urgent' if issue['laboratory_flags'] else 'untriaged'
        issue['priority_basis']=priority['basis'] if priority else 'Explicit laboratory critical flag' if issue['laboratory_flags'] else 'No clinician priority assigned'
        issue['source_count']=len(ids);issue['display_date']=day(issue['sort_date']).isoformat()
        # Clearly dated context is detail-only, excluded from issue/source counts.
        if issue['id'] in ('low-haemoglobin','critical-sodium','stable-renal'):
            issue['context']=[contribution(source['previous-pathology'],snapshot,period,True)]
    def order(i):
        rank={'urgent':0,'attention':1,'routine':2,'untriaged':3}
        # Preserve explicit source-critical ordering even after a clinician changes priority.
        tier=0 if i['laboratory_flags'] else rank[i['priority']]
        moment=instant(i['sort_date']) or datetime.fromisoformat(i['sort_date']).replace(tzinfo=SYDNEY)
        return (tier,-moment.timestamp(),i['id'])
    result=sorted(issues.values(),key=order)
    def upcoming(a):
        moment=datetime.fromisoformat(a['date']+'T'+(a.get('time') or '23:59')).replace(tzinfo=SYDNEY)
        return moment>=now and a.get('status') not in ('Completed','Attended','Cancelled','Canceled')
    upcoming_rows=[dict(a,source_url='/clinician/calendar#'+a['id']) for a in appointments if a.get('kind')=='appointment' and upcoming(a)][:3]
    return dict(clinician={k:account[k] for k in ('username','name','role','salutation')},patient={'name':identity['full_name'],'date_of_birth':identity['date_of_birth'],'facility':identity['address'].split('\n')[0]},
        today=now.date().isoformat(),last_consultation=period.last,comparison_date=period.data['start_date'],comparison_period=period.data,first_review=not period.last,
        upcoming=upcoming_rows[0] if upcoming_rows else None,upcoming_appointments=upcoming_rows,past_attendance_unknown=[],
        issues=result,groups=groups,summary_kind='Prepared fictional demo summaries · require clinician review',receipt_limitation='Event, document, fictional receipt and actual import dates are distinct.',
        workflow_enabled=True,recipient_simulation='Patient and Emily share one demo workspace; recipient separation is simulated, not individual authenticated access.',
        tab_counts={'needs_review':sum(not i['workflow']['reviewed'] for i in result),'action_required':sum(any(not a['resolved'] for a in i['workflow']['actions']) for i in result),'reviewed':sum(i['workflow']['reviewed'] for i in result)})

def valid_recipients(body,events):
    recipients=body.get('recipients')
    authorisation=next((e.payload['review_recipient_authorisation'] for e in reversed(events) if 'review_recipient_authorisation' in e.payload),{})
    if not isinstance(recipients,list) or not recipients or any(r not in authorisation for r in recipients):raise HTTPException(400,'Select the patient and/or an authorised carer.')
    return sorted(set(recipients))

def text(body,key,maxlen=1000,required=True):
    value=body.get(key,'')
    if not isinstance(value,str) or len(value.strip())>maxlen or (required and not value.strip()):raise HTTPException(400,'Check '+key.replace('_',' ')+'.')
    return value.strip()

def mutate(state,account,key,body):
    if not isinstance(body,dict):raise HTTPException(400,'Check the review form.')
    kind=body.get('kind')
    if kind not in KINDS:raise HTTPException(400,'Unknown review action.')
    with state.lock:
        snapshot=state.snapshot();source=sources(snapshot)
        members={id:p for id,p in source.items() if issue_key(p)==key}
        if not members:raise HTTPException(404,'Issue not found.')
        # Same assignment check as the dashboard, without imposing a personal comparison period.
        dashboard(state,account)
        p={'kind':kind,'issue':key,'actor':{k:account[k] for k in ('username','name','role')}}
        current=states(snapshot.events,key,list(members))
        if kind=='review':
            ids=body.get('sources')
            if not isinstance(ids,list) or not ids or any(id not in members for id in ids):raise HTTPException(400,'Select the contributions being reviewed.')
            p['sources']=sorted(set(ids))
            p['source_artifacts']={id:members[id].get('artifact') for id in sorted(set(ids))}
            if set(ids)<=set(current['reviewed_sources']):return {'duplicate':True}
        elif kind=='priority':
            if body.get('priority') not in ('urgent','attention','routine','untriaged'):raise HTTPException(400,'Select a valid priority.')
            p.update(priority=body['priority'],basis=text(body,'basis'))
        elif kind=='action_created':
            p.update(description=text(body,'description'),timeframe=text(body,'timeframe',200))
        elif kind in ('communication','appointment_request','release'):
            p['recipients']=valid_recipients(body,snapshot.events)
            if kind in ('communication','appointment_request'):p['source_artifacts']={id:member.get('artifact') for id,member in members.items()}
            p['message']=text(body,'message',required=kind!='release')
            if body.get('confirmed') is not True:raise HTTPException(400,'Preview and confirm before sending or releasing.')
            if kind=='communication':
                method=body.get('method')
                if method not in ('phone','in_person','in_app'):raise HTTPException(400,'Select a communication method.')
                p['method']=method
                if method=='in_app':p['communication_at']=envelope.format_ts(state.now())
                else:
                    stamp=instant(body.get('communication_at'))
                    if not stamp or stamp>state.now().astimezone(SYDNEY):raise HTTPException(400,'Enter the actual communication date/time, no later than now.')
                    p['communication_at']=stamp.isoformat()
                p['delivery']='Sent in-app; acknowledgement not recorded' if method=='in_app' else 'Clinician-recorded communication'
            elif kind=='appointment_request':
                p.update(timeframe=text(body,'timeframe',200),method='in_app',delivery='Appointment requested; not booked')
            else:
                id=body.get('source')
                if id not in members or members[id]['access']!='clinical':raise HTTPException(400,'Select one clinical document to release.')
                if id not in current['reviewed_sources']:raise HTTPException(409,'Review this document before releasing it.')
                p.update(source=id,artifact=members[id]['artifact'],title=members[id]['title'])
                if any(a['source']==id and set(p['recipients'])<=set(a['recipients']) and a['message']==p['message'] for a in current['releases']):return {'duplicate':True}
        else:
            action=body.get('action')
            if not any(a['id']==action and not a['resolved'] for a in current['actions']):raise HTTPException(400,'No outstanding linked action with this ID.')
            p.update(action=action,basis=text(body,'basis'))
        # Stable submission ID plus semantic deduplication protect retried/double clicks.
        token=text(body,'request_id',100)
        p['request_id']=token
        semantic={k:v for k,v in p.items() if k not in ('request_id','actor','communication_at')}
        if kind=='communication' and p['method']!='in_app':semantic['communication_at']=p['communication_at']
        digest=hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest()
        p['fingerprint']=digest
        previous=history(snapshot.events)
        existing=next((a for a in previous if (a.get('request_id')==token and a['actor']['username']==account['username']) or (a.get('fingerprint')==digest and (kind not in ('appointment_request','action_created') or not any(h['kind']=='action_resolved' and h.get('action')==a['id'] for h in previous)))),None)
        if existing:return {'duplicate':True,'event':existing['id']}
        event=envelope.new('note.recorded',state.vault.identity.id,ts=envelope.format_ts(state.now()),payload={'review_workflow':p})
        state.append(event)
        return {'event':event.id,'duplicate':False}


def public_feed(state):
    snapshot=state.snapshot();audit=history(snapshot.events)
    messages=[a for a in audit if a['id'] not in retracted(snapshot.events) and (a['kind']=='appointment_request' or (a['kind']=='communication' and a['method']=='in_app'))]
    acks=[dict(e.payload['review_acknowledgement'],at=e.ts) for e in snapshot.events if 'review_acknowledgement' in e.payload]
    from .patient_portal import bookings
    booked={b['request']:b for b in bookings(snapshot.events)}
    closed={a['action'] for a in audit if a['kind']=='action_resolved'}
    return dict(simulation='Shared patient/carer demo view. Recipients are recorded, but individual recipient access is simulated.',
        messages=[{k:a[k] for k in ('id','kind','at','message','recipients','actor','timeframe') if k in a}|{'acknowledgements':[x for x in acks if x['message']==a['id']],'booking':booked.get(a['id']),'action_resolved':a['id'] in closed,'status':'Appointment booked; discussion pending' if a['id'] in booked else 'Request closed' if a['id'] in closed else 'Appointment requested; not booked' if a['kind']=='appointment_request' else 'Sent in-app'} for a in messages],
        documents=[{k:a[k] for k in ('id','at','message','recipients','actor','artifact','title')} for a in audit if a['kind']=='release'])


def release_for(events,artifact):
    releases=[]
    for event in events:
        action=event.payload.get('document_release') or event.payload.get('review_workflow') or {}
        if action.get('kind')=='release' and action.get('artifact')==artifact:
            releases.append({'date':day(event.ts).isoformat(),'released_by':action['actor']['name'],
                             'recipients':action['recipients'],'message':action['message']})
    return releases[-1] if releases else None
