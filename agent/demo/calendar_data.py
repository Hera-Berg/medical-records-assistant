"""Explicit fictional bookings and facility staff, separate from private note text."""
CARE_TEAM = [
 {'name':'Dr James Smith','role':'Usual GP','organisation':'Sydney Aged Care','facility':True},
 {'name':'Morgan Bell','role':'Registered nurse','organisation':'Example Gardens Residential Aged Care','facility':True},
 {'name':'Dr Olivia Bennett','role':'Locum GP','organisation':'Sydney Aged Care','facility':True},
 {'name':'Dr Liam Wilson','role':'Locum GP','organisation':'Sydney Aged Care','facility':True},
 {'name':'Dr Matthew Thompson','role':'Cardiologist','organisation':'Harbourside Cardiology','facility':False},
 {'name':'Dr Amelia Patel','role':'Pharmacist','organisation':'Harbour Clinical Pharmacy','facility':False},
]
BOOKINGS = [
 {'id':'locum-2025-03-26','date':'2025-03-26','time':'09:30','title':'Facility GP review','professional':'Dr Olivia Bennett','location':'Example Gardens · GP room','kind':'appointment','status':'Attended'},
 {'id':'locum-2026-03-18','date':'2026-03-18','time':'10:00','title':'Facility GP cover review','professional':'Dr Liam Wilson','location':'Example Gardens · GP room','kind':'appointment','status':'Attended'},
 {'id':'podiatry-2026-10-28','date':'2026-10-28','time':'10:30','title':'Podiatry appointment','professional':'Daniel Brooks, podiatrist','location':'Example Gardens · allied health room','kind':'appointment','status':'Booked'},
 {'id':'gp-2026-11-03','date':'2026-11-03','time':'09:30','title':'GP review — walking confidence and medicines','professional':'Dr James Smith','location':'Example Gardens · GP room, Sydney Aged Care','kind':'appointment','status':'Booked'},
 {'id':'physio-2026-11-12','date':'2026-11-12','time':'11:00','title':'Physiotherapy — balance and walking','professional':'Sarah Green, physiotherapist','location':'Example Gardens · therapy room','kind':'appointment','status':'Booked'},
 {'id':'hearing-2026-11-18','date':'2026-11-18','time':'14:00','title':'Hearing-aid review','professional':'Olivia Reed, audiologist','location':'Example Community Audiology · 2 Demonstration Road, Sydney','kind':'appointment','status':'Booked'},
 {'id':'cardio-2026-11-24','date':'2026-11-24','time':'10:30','title':'Cardiology follow-up','professional':'Dr Matthew Thompson','location':'Harbourside Cardiology · 14 Example Wharf, Sydney','kind':'appointment','status':'Booked'},
 {'id':'care-plan-2027-01-15','date':'2027-01-15','time':'10:00','title':'Care-plan review with Jane and Emily','professional':'Dr James Smith and Morgan Bell, registered nurse','location':'Example Gardens · family meeting room','kind':'event','status':'Planned'},
]


def appointment(item):
    if item['event_type'] not in ('appointment','hospital','discharge','vaccination'):
        return None
    who=item['author']
    if 'Thompson' in who:
        time='10:30'; where='Harbourside Cardiology · 14 Example Wharf, Sydney'
    elif 'Smith' in who:
        time='09:30'; where='Example Gardens · GP room, Sydney Aged Care'
    elif 'physiotherapist' in who:
        time='11:00'; where='Example Gardens · therapy room'
    elif 'podiatrist' in who:
        time='10:30'; where='Example Gardens · allied health room'
    elif 'audiologist' in who:
        time='14:00'; where='Example Community Audiology · 2 Demonstration Road, Sydney'
    elif 'emergency physician' in who:
        time=None; where='Example District Hospital'
    else:
        time=None; where='Example Gardens Residential Aged Care'
    return {'professional':who,'time':time,'location':where,'status':'Attended'}


def seed(vault):
    from ..demo_metadata import SCENARIO
    from ..events import envelope
    existing={}
    for event in vault.read().events:
        key=event.payload.get('demo_calendar_key')
        if key and event.payload.get('demo_scenario')==SCENARIO:
            existing[key]=event.payload
    payloads=[{'demo_calendar_key':'care-team','care_team':CARE_TEAM},
              {'demo_calendar_key':'birthday-november-v1'}]
    payloads += [{'demo_calendar_key':b['id'],'calendar_id':'demo:'+b['id'],'calendar_entry':b} for b in BOOKINGS]
    for entry in payloads:
        payload={**entry,'calendar_only':True,'demo_scenario':SCENARIO,'fictional':True}
        if existing.get(entry['demo_calendar_key'])==payload:
            continue
        vault.append(envelope.new('note.recorded',vault.identity.id,payload=payload))
