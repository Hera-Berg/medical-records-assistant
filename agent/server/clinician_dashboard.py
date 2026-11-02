"""Deterministic source excerpts since this account's own completed consultation.

No clinical inference, priorities, review completion or task state is created.
"""
from datetime import date
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .. import profile, care_team
from ..demo_metadata import entries
from .routes.calendar import calendar_events


def build(state, account, comparison_date=None):
    snapshot = state.snapshot()
    today = state.now().astimezone(ZoneInfo('Australia/Sydney')).date()
    identity = profile.read(snapshot.events)['values']
    if identity.get('full_name') != 'Jane Doe':
        raise HTTPException(403, 'This account is assigned only to Jane’s demonstration record.')
    team = care_team.listing(snapshot.events, snapshot.artifacts, identity)
    member = next((m for m in team if care_team.key(m['name']) == care_team.key(account['name'])), None)
    if not member:
        raise HTTPException(403, 'This clinician is not assigned to this care team.')
    appointments = calendar_events(snapshot, state.now())
    completed = [a for a in appointments if a.get('status')=='Attended'
                 and a.get('kind')=='appointment' and a['date']<=today.isoformat()
                 and care_team.key(a.get('professional','').split(', ')[0]) == care_team.key(account['name'])]
    last = max(completed, key=lambda a:(a['date'],a.get('time') or ''), default=None)
    cutoff = comparison_date or (last['date'] if last else None)
    if cutoff:
        try:
            parsed = date.fromisoformat(cutoff)
            if parsed.isoformat()!=cutoff or parsed>today:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, 'Choose a valid comparison date on or before today.') from None
    follow_up = [a for a in appointments if a.get('status') in ('Booked','Planned')
                 and a.get('kind') in ('appointment','event')]
    upcoming = [a for a in appointments if a.get('kind')=='appointment'
                and a['date']>=today.isoformat() and a.get('status')=='Booked']
    upcoming.sort(key=lambda a:(a['date'], a.get('time') or ''))
    groups = {key:[] for key in ('reported', 'documents', 'medications', 'follow_up')}
    if cutoff:
        for event in sorted(entries(snapshot.events, snapshot.artifacts).values(),
                            key=lambda e:e.payload['event_date'], reverse=True):
            p = event.payload
            if not cutoff < p['event_date'] <= today.isoformat():
                continue
            doc = p.get('native_document') or {}
            author = p['author']
            professional = next((m for m in team if care_team.key(m['name'])==care_team.key(author.split(', ')[0])), None)
            role = professional['role'] if professional else ('Patient' if author=='Jane Doe' else doc.get('specialty') or author.partition(', ')[2] or 'Source contributor')
            item = {'id':p['demo_item_id'], 'headline':p['title'], 'summary':p['text'],
                    'author':author.split(', ')[0], 'role':role, 'date':p['event_date'],
                    'source_url':'/clinician/artifact/'+p['artifact'],
                    'status':'Prepared demo source excerpt · awaiting clinician review'}
            if author=='Jane Doe' or author.startswith('Emily Doe') or 'nursing' in p['source_type'].lower():
                groups['reported'].append(item)
            else:
                groups['documents'].append(item)
            # Only issued prescriptions/charts with explicit change wording;
            # follow-up correspondence saying "no change" never becomes a change.
            title = p['title'].lower()
            if p['event_type']=='medication' and ('prescri' in title or 'medication chart' in title):
                groups['medications'].append(item)
        # Explicit booked follow-ups are schedules, not inferred clinical tasks.
        for a in sorted(follow_up, key=lambda a:(a['date'],a.get('time') or '')):
            groups['follow_up'].append({'id':a['id'], 'headline':a['title'],
                'summary':'Booked appointment' + (' with '+a['professional'] if a.get('professional') else '') + (' at '+a['location'] if a.get('location') else '') + '. Completion is not recorded.',
                'author':'Author not recorded', 'role':'Calendar booking',
                'date':a['date'], 'source_url':('/clinician'+a['source_url']) if a.get('source_url','').startswith('/artifact/') else '/clinician/calendar',
                'status':'Recorded booking · completion not recorded'})
    return {'clinician':{k:account[k] for k in ('username','name','role','salutation')},
        'patient':{'name':identity.get('full_name'), 'date_of_birth':identity.get('date_of_birth'),
                   'facility':identity.get('address','').split('\n')[0]},
        'today':today.isoformat(), 'last_consultation':last, 'upcoming':upcoming[0] if upcoming else None,
        'comparison_date':cutoff, 'first_review':last is None, 'groups':groups,
        'summary_kind':'Prepared demonstration source excerpts from Jane’s authored records; no live AI generation or clinician review is recorded.'}
