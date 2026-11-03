"""Deterministic source excerpts since this account's own completed consultation.

No clinical inference, priorities, review completion or task state is created.
"""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .. import profile, care_team
from ..demo_metadata import entries, source_ids
from .routes.calendar import calendar_events
from .comparison import resolve, day, instant


def concise(text):
    sentences = re.split(r'(?<!Dr)(?<!Mrs)(?<!Mr)(?<=[.!?])\s+(?=[A-Z“])', text.strip())
    factual = [s for s in sentences if not s.startswith(('This demo', 'This summary', 'This note does not', 'Release wording', 'Fictional influenza', 'Seeded transcription'))]
    return ' '.join((factual or sentences)[:2])


def build(state, account, comparison_date=None):
    snapshot = state.snapshot()
    from .review_workflow import enabled, dashboard
    if enabled(snapshot.events):return dashboard(state,account,comparison_date)
    local_now = state.now().astimezone(ZoneInfo('Australia/Sydney'))
    today = local_now.date()
    identity = profile.read(snapshot.events)['values']
    if identity.get('full_name') != 'Jane Doe':
        raise HTTPException(403, 'This account is assigned only to Jane’s demonstration record.')
    team = care_team.listing(snapshot.events, snapshot.artifacts, identity)
    member = next((m for m in team if care_team.key(m['name']) == care_team.key(account['name'])), None)
    if not member:
        raise HTTPException(403, 'This clinician is not assigned to this care team.')
    appointments = calendar_events(snapshot, state.now())
    period = resolve(appointments,account,local_now,comparison_date)
    last = period.last
    cutoff = period.start_day.isoformat() if period.start_day else None
    scheduled = [dict(a) for a in appointments if a.get('kind')=='appointment'
                 and a.get('status') not in ('Attended', 'Completed', 'Cancelled', 'Canceled')]
    def is_past(appointment):
        if appointment['date'] != today.isoformat():
            return appointment['date'] < today.isoformat()
        if appointment.get('time'):
            instant = datetime.fromisoformat(appointment['date']+'T'+appointment['time']).replace(tzinfo=ZoneInfo('Australia/Sydney'))
            return instant < local_now
        return False
    for appointment in scheduled:
        appointment['attendance'] = 'Attendance unknown' if is_past(appointment) else appointment.get('status', 'Scheduled')
        appointment['source_url'] = '/clinician/calendar#' + appointment['id']
    upcoming = sorted([a for a in scheduled if not is_past(a)],
                      key=lambda a:(a['date'], a.get('time') or ''))
    past_unknown = sorted([a for a in scheduled if is_past(a)], key=lambda a:a['date'], reverse=True)
    groups = {key:[] for key in ('reported', 'documents', 'medications', 'follow_up', 'newly_received')}
    if cutoff:
        for event in sorted(entries(snapshot.events, snapshot.artifacts).values(),
                            key=lambda e:e.payload['event_date'], reverse=True):
            p = event.payload
            doc = p.get('native_document') or {}
            # Event, authoring and receipt/upload clocks are independent.
            clinical = p.get('observation_timestamp') or p.get('event_timestamp') or p.get('observation_date') or p['event_date']
            if (day(clinical) and day(clinical)>today) or (instant(clinical) and instant(clinical)>local_now):
                continue
            position = period.position(clinical)
            artifact = snapshot.artifacts.get(p['artifact'])
            authored = doc.get('authored_at') or doc.get('authored_date')
            document_date = doc.get('date')
            received = p.get('received_at') or doc.get('received_at') or doc.get('received_date')
            uploaded = artifact.ingested_ts if artifact else None
            receipt_in_period = period.position(received) or period.position(uploaded)
            older = day(clinical) is not None and (day(clinical) < period.start_day or (instant(clinical) and period.start and instant(clinical) <= period.start))
            is_request = p['demo_item_id']=='jane-history-68'
            if last and last.get('source_url') == '/artifact/'+p['artifact']:
                continue
            if not position and not (older and receipt_in_period) and not is_request:
                continue
            author = p['author']
            professional = next((m for m in team if care_team.key(m['name'])==care_team.key(author.split(', ')[0])), None)
            role = professional['role'] if professional else ('Patient' if author=='Jane Doe' else doc.get('specialty') or author.partition(', ')[2] or 'Source contributor')
            if author.startswith('Emily Doe'):
                role = 'Daughter / family carer'
            item = {'id':p['demo_item_id'], 'headline':p['title'], 'summary':concise(p['text']),
                    'author':author.split(', ')[0], 'role':role, 'date':day(clinical).isoformat() if day(clinical) else p['event_date'],
                    'source_url':'/clinician/artifact/'+p['artifact'],
                    'status':'Prepared demo summary',
                    'event_date':day(clinical).isoformat() if day(clinical) else None,
                    'event_timestamp':instant(clinical).isoformat() if instant(clinical) else None,
                    'authored_date':day(authored).isoformat() if day(authored) else None,
                    'authored_at':instant(authored).isoformat() if instant(authored) else None,
                    'document_date':day(document_date).isoformat() if day(document_date) else None,
                    'received_date':day(received).isoformat() if day(received) else None,
                    'received_at':instant(received).isoformat() if instant(received) else None,
                    'uploaded_at':uploaded,
                    'ordering':'Same consultation day; ordering unknown' if position=='same-day-unknown' else 'Receipt/upload on consultation day; ordering unknown' if receipt_in_period=='same-day-unknown' else None}
            headlines = {
                'jane-review-patient-2026-10-23':'Jane reports staying in her room for breakfast',
                'jane-review-carer-2026-10-24':'Emily reports Jane asking for nearby walking support',
                'jane-review-staff-2026-10-25':'Morgan records Jane’s request for nearby staff support',
            }
            item['headline'] = headlines.get(p['demo_item_id'], item['headline'])
            # A documented request is not a calendar booking. This explicit
            # source says the question remains for discussion; no completion is recorded.
            if p['demo_item_id']=='jane-history-68':
                item['headline'] = 'Jane requests clarification about her heart medication'
                item['summary'] = 'Jane asks which heart tablet changed in August and wants to discuss her current medication list at the next appointment.'
                item['outside_period'] = position is None
                groups['follow_up'].append(item)
            elif not position and older and receipt_in_period:
                item['date_basis'] = 'Historical content; newly uploaded/received, not a newly occurring clinical change'
                groups['newly_received'].append(item)
            elif author=='Jane Doe' or author.startswith('Emily Doe') or 'nursing' in p['source_type'].lower():
                groups['reported'].append(item)
            else:
                groups['documents'].append(item)
            # Only issued prescriptions/charts with explicit change wording;
            # follow-up correspondence saying "no change" never becomes a change.
            title = p['title'].lower()
            if position and p['event_type']=='medication' and ('prescri' in title or 'medication chart' in title):
                groups['medications'].append(item)
        # Ordinary uploaded documents share the actual ingest clock. Without a
        # dated original, do not infer an event/onset or historical findings.
        authored_sources = source_ids(snapshot.events, snapshot.artifacts)
        for short, artifact in snapshot.artifacts.items():
            if short in authored_sources or not period.position(artifact.ingested_ts):
                continue
            original = day(artifact.artifact_ts)
            groups['newly_received'].append({'id':short, 'headline':'Document newly uploaded to the record',
                'summary':'Historical document; its contents are not newly occurring clinical changes.' if original and original < period.start_day else 'Document uploaded during the period; a clinical event date is not supplied.' if original else 'Original document date is not recorded. See the source; no event date or clinical change is inferred.',
                'author':'Author not recorded', 'role':'Uploaded document',
                'date':original.isoformat() if original else day(artifact.ingested_ts).isoformat(),
                'event_date':None, 'authored_date':None, 'document_date':original.isoformat() if original else None,
                'received_date':None, 'uploaded_at':artifact.ingested_ts,
                'source_url':'/clinician/artifact/'+short, 'status':'Original uploaded document',
                'date_basis':'New upload/import; clinical receipt date not recorded'})
        # Typed contributions are original note events, not generated demo
        # fixtures. Their recorded date is known; the observation date/author is not.
        for event in snapshot.events:
            p = event.payload
            if event.type != 'note.recorded' or p.get('source') != 'typed' or p.get('demo_scenario') or not isinstance(p.get('text'), str):
                continue
            recorded = datetime.fromisoformat(event.ts.replace('Z','+00:00')).astimezone(ZoneInfo('Australia/Sydney')).date().isoformat()
            position = period.position(p.get('observation_timestamp') or p.get('observation_date') or event.ts)
            if position:
                text = p['text'].strip()
                # Explicit accidental demo test entry only; original event stays recoverable.
                if state.vault.is_demo and event.id == '01M62NQPN2MQ6VB8DJ9ZJHVTEP' and text.casefold() == 'why i':
                    continue
                groups['reported'].append({'id':event.id, 'headline':'Typed note: '+text,
                    'summary':text, 'author':'Author not recorded', 'role':'Typed record contribution',
                    'date':day(p.get('observation_timestamp') or p.get('observation_date')).isoformat() if day(p.get('observation_timestamp') or p.get('observation_date')) else recorded,
                    'event_date':day(p.get('observation_timestamp') or p.get('observation_date')).isoformat() if day(p.get('observation_timestamp') or p.get('observation_date')) else None,
                    'recorded_at':event.ts, 'authored_date':None, 'received_date':None, 'uploaded_at':None,
                    'date_basis':'Observation date supplied' if p.get('observation_timestamp') or p.get('observation_date') else 'Recorded date · Observation date not supplied',
                    'ordering':'Same consultation day; ordering unknown' if position=='same-day-unknown' else None,
                    'source_url':'/clinician/observation/'+event.id, 'status':'Original typed note'})
        for rows in groups.values():
            rows.sort(key=lambda row:(row['date'],row['id']), reverse=True)
    from .review_issues import project
    originals = {e.payload['demo_item_id']:e.payload['text'] for e in entries(snapshot.events, snapshot.artifacts).values()}
    originals.update({e.id:e.payload['text'] for e in snapshot.events if e.type=='note.recorded' and isinstance(e.payload.get('text'),str) and e.payload.get('source')=='typed'})
    issues = project(groups, originals)
    return {'issues':issues, 'clinician':{k:account[k] for k in ('username','name','role','salutation')},
        'patient':{'name':identity.get('full_name'), 'date_of_birth':identity.get('date_of_birth'),
                   'facility':identity.get('address','').split('\n')[0]},
        'today':today.isoformat(), 'last_consultation':last, 'upcoming':upcoming[0] if upcoming else None,
        'upcoming_appointments':upcoming[:3], 'past_attendance_unknown':past_unknown,
        'comparison_date':cutoff, 'comparison_period':period.data,
        'receipt_limitation':'Clinical receipt dates are not recorded for some existing sources. Actual upload/import timestamps are shown where available; they do not establish clinical receipt dates.', 'first_review':last is None, 'groups':groups,
        'summary_kind':'Prepared fictional demo summaries require clinician review. Original typed notes are labelled separately.'}
