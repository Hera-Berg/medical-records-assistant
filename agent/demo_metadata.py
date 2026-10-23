"""Patient-visible authored demo metadata carried by existing note events.

This does not read sealed compartments, infer authorship, or grant permissions.
Only registered original artefacts can be sources. Names are fictional attribution,
not authenticated identities. Ordinary notes and uploads retain their behaviour.
"""
SCENARIO = 'jane-two-year-v1'


def history(events, artifacts):
    """Valid demo notes, including earlier authored versions (never deleted)."""
    for event in events:
        p = event.payload
        if (event.type == 'note.recorded' and p.get('demo_scenario') == SCENARIO
                and p.get('fictional') is True and p.get('patient_visible') is True
                and all(isinstance(p.get(key), str) for key in
                        ('demo_item_id','artifact','title','text','author','event_type','source_type','event_date','status'))
                and p['artifact'] in artifacts):
            yield event


def entries(events, artifacts):
    latest = {}
    for event in history(events, artifacts):
        key = event.payload['demo_item_id']
        if key not in latest or event.sort_key > latest[key].sort_key:
            latest[key] = event
    return latest


def source_ids(events, artifacts):
    """All fixture originals, including preserved earlier versions."""
    return {short for event in history(events, artifacts)
            for short in (event.payload['artifact'], event.payload.get('original_wording_artifact'))
            if isinstance(short, str) and short in artifacts}
