"""Appointments, explicit prescription expiries and birthdays; never sealed notes."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Body, HTTPException
from ...events import envelope
from ... import profile
from ...prescriptions import listing
from ...demo_metadata import entries, SCENARIO
from ..deps import get_state
from ..state import RecordState

router=APIRouter()
SYDNEY=ZoneInfo('Australia/Sydney')


def team(events):
    result=[]
    for event in events:
        if event.type=='note.recorded' and event.payload.get('demo_scenario')==SCENARIO and 'care_team' in event.payload:
            result=event.payload['care_team']
    return result


def calendar_events(snapshot,now):
    records={}
    for event in snapshot.events:
        p=event.payload
        if event.type=='note.recorded' and p.get('calendar_only') is True and isinstance(p.get('calendar_entry'),dict):
            records[p['calendar_id']]={**p['calendar_entry'],'id':p['calendar_id'],
                'fictional':p.get('fictional') is True,'added_at':event.ts,'editable':not p.get('fictional')}
    for event in entries(snapshot.events,snapshot.artifacts).values():
        p=event.payload; document=p.get('native_document',{})
        appointment=document.get('calendar_appointment')
        if appointment:
            key='source:'+p['demo_item_id']
            records[key]={'id':key,'date':p['event_date'],'title':p['title'],
                          'kind':'appointment','fictional':True,'source_url':'/artifact/'+p['artifact'],
                          **appointment,'editable':False}
    for row in listing(snapshot.events,snapshot.artifacts,now,snapshot.projection):
        if row.get('expiry_date'):
            key='expiry:'+row['id']
            records[key]={'id':key,'date':row['expiry_date'],'title':row['medicine']+' prescription expiry',
                'kind':'prescription','time':None,'professional':row['prescriber'], 'location':None,
                'status':row['treatment_status'],'source_url':row['source_url'],'fictional':row['fictional'],
                'description':'Prescription validity ends on this day. This does not stop the medicine.',
                'expiry_state':row['expiry']['state'],'editable':False}
    born=profile.read(snapshot.events)['values']['date_of_birth']
    today=now.astimezone(SYDNEY).date()
    try:
        dob=date.fromisoformat(born)
        name=profile.read(snapshot.events)['values']['preferred_name'] or 'Patient'
        for year in range(today.year-2,today.year+3):
            try: birthday=date(year,dob.month,dob.day)
            except ValueError: continue
            key='birthday:'+str(year)
            records[key]={'id':key,'date':birthday.isoformat(),'time':None,'title':name+'’s birthday',
                'kind':'birthday','description':f'Turns {year-dob.year}.','fictional':bool(entries(snapshot.events,snapshot.artifacts)),
                'source_url':'/profile','editable':False}
    except ValueError:
        pass
    return sorted(records.values(),key=lambda r:(r['date'],r.get('time') or '',r['id']))


@router.get('/api/calendar')
def get_calendar(state:RecordState=Depends(get_state)):
    now=state.now()
    return {'today':now.astimezone(SYDNEY).date().isoformat(),'timezone':'Australia/Sydney',
            'events':calendar_events(state.snapshot(),now)}


@router.post('/api/calendar',status_code=201)
def add_event(body:dict=Body(...),state:RecordState=Depends(get_state)):
    allowed={'id','title','date','time','professional','location','description','kind'}
    if set(body)-allowed or any(not isinstance(v,str) for v in body.values()):
        raise HTTPException(400,'Use text for the event details.')
    cleaned={k:v.strip() for k,v in body.items()}
    if not cleaned.get('title') or len(cleaned['title'])>160:
        raise HTTPException(400,'Enter an event title of at most 160 characters.')
    try:
        if date.fromisoformat(cleaned.get('date','')).isoformat()!=cleaned['date']: raise ValueError()
        if cleaned.get('time') and datetime.strptime(cleaned['time'],'%H:%M').strftime('%H:%M')!=cleaned['time']: raise ValueError()
    except ValueError:
        raise HTTPException(400,'Enter a valid date and time.') from None
    cleaned.setdefault('kind','appointment')
    if cleaned.get('time'):
        local=datetime.fromisoformat(cleaned['date']+'T'+cleaned['time']).replace(tzinfo=SYDNEY)
        if local.astimezone(ZoneInfo('UTC')).astimezone(SYDNEY).replace(tzinfo=None)!=local.replace(tzinfo=None):
            raise HTTPException(400,'That time does not exist in Sydney because the clocks change. Choose another time.')
    if any(len(v)>2000 for v in cleaned.values()) or cleaned.get('kind','appointment') not in ('appointment','event'):
        raise HTTPException(400,'Check the event details.')
    identifier=cleaned.pop('id',None) or 'user:'+envelope.ulid.new()
    if not identifier.startswith('user:') or len(identifier)>80:
        raise HTTPException(400,'This calendar entry cannot be changed here.')
    payload={'calendar_only':True,'calendar_id':identifier,'calendar_entry':cleaned}
    with state.lock:
        previous=next((e.payload for e in reversed(state.snapshot().events) if e.payload.get('calendar_id')==identifier),None)
        if previous!=payload:
            state.append(envelope.new('note.recorded',state.vault.identity.id,payload=payload))
    return {'id':identifier}
