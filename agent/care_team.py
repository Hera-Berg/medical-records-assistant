"""Current patient-visible care relationships, shared by profile and care-team views.

An explicit roster or follow-up booking establishes membership. Document authors
only enrich those relationships; they never become current members by authorship.
Sealed notes are not read. This projection does not modify the record.
"""
import re
from .demo_metadata import SCENARIO, entries


def key(name):
    name = re.sub(r'\s*\(fictional\)', '', name, flags=re.I)
    return re.sub(r'[^a-z0-9]', '', re.sub(r'^dr\.?\s+', '', name.strip(), flags=re.I).casefold())


def listing(events, artifacts, values):
    from .record_identity import view_events
    events = view_events(events)
    members = {}

    def add(name, role, organisation='', facility=False, **extra):
        if not name or not role:
            return
        identifier = key(name)
        member = members.setdefault(identifier, {'id': identifier, 'name': name,
            'role': role, 'organisation': organisation, 'facility': facility,
            'kind': 'person', 'group': 'facility' if facility else 'external'})
        for field, value in extra.items():
            if value:
                member[field] = value
        if organisation and not member['organisation']:
            member['organisation'] = organisation
        return member

    roster = []
    bookings = {}
    for event in events:
        p = event.payload
        if event.type != 'note.recorded' or p.get('demo_scenario') != SCENARIO:
            continue
        if isinstance(p.get('care_team'), list):
            roster = p['care_team']
        booking = p.get('calendar_entry')
        if isinstance(booking, dict) and p.get('calendar_id'):
            bookings[p['calendar_id']] = booking
    for member in roster:
        add(member['name'], member['role'], member.get('organisation', ''), member.get('facility', False), **{k:member[k] for k in ('location','phone','kind','group') if member.get(k)})

    # Explicit follow-up bookings name ongoing allied-health relationships which
    # the older facility roster did not contain. Do not parse combined attendees.
    roles = {'physiotherapist': 'Physiotherapist', 'audiologist': 'Audiologist', 'podiatrist': 'Podiatrist'}
    for booking in sorted(bookings.values(), key=lambda b: b.get('date', '')):
        who = booking.get('professional', '')
        if ' and ' in who or booking.get('status') not in ('Booked', 'Planned'):
            continue
        name, _, suffix = who.partition(', ')
        member = members.get(key(name))
        if member is None and suffix.casefold() in roles:
            member = add(name, roles[suffix.casefold()])
        if member and booking.get('location'):
            member['location'] = booking['location']
            # Audiology's practice is explicitly named in this booking. Other
            # practice names come from the roster or matching native documents.
            if suffix.casefold() == 'audiologist':
                member['organisation'] = booking['location'].split(' · ')[0]

    documents = sorted(entries(events, artifacts).values(), key=lambda e: e.payload['event_date'])
    for event in documents:
        p = event.payload
        member = members.get(key(p['author'].split(', ')[0]))
        doc = p.get('native_document') or {}
        if member:
            if not member['organisation'] and doc.get('organisation'):
                member['organisation'] = doc['organisation']
            appointment = doc.get('calendar_appointment') or {}
            if not member.get('location') and appointment.get('location'):
                member['location'] = appointment['location']

    # User-entered profile values remain authoritative for the usual GP/pharmacy.
    gp_name = values.get('gp_name', '')
    if gp_name:
        gp = add(gp_name, 'Usual GP', values.get('gp_practice', ''))
        gp['organisation'] = values.get('gp_practice', '')
        if values.get('gp_phone'):
            gp['phone'] = values['gp_phone']
    pharmacy = values.get('pharmacy', '')
    if pharmacy:
        member = add(pharmacy, 'Pharmacy', kind='organisation')
        if values.get('pharmacy_phone'):
            member['phone'] = values['pharmacy_phone']

    # A family relationship is included only when the profile explicitly calls
    # them a carer. Being next of kin alone does not establish a care role.
    if values.get('emergency_name') and 'carer' in values.get('emergency_relationship', '').casefold():
        add(values['emergency_name'], values['emergency_relationship'], group='family')
    return list(members.values())
