"""Patient-visible authored demo metadata carried by existing note events.

This does not read sealed compartments, infer authorship, or grant permissions.
Only registered original artefacts can be sources. Names are fictional attribution,
not authenticated identities. Ordinary notes and uploads retain their behaviour.
"""
SCENARIO = 'jane-two-year-v1'


def is_sample(events):
    """Whether these events are the sample record that ships with the app.

    The sample is written without demonstration disclaimers in what a person
    reads: the workspace switch already says which record is open. This is the
    one test every such wording choice makes, set by ``agent/demo/sample.py``.
    """
    return any(e.type == 'note.recorded' and e.payload.get('sample_record') is True for e in events)


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


def readable_sections(payload):
    """Search the same public native edition the document page displays.

    Called only for validated patient-visible demo entries. No file access,
    sealed events, earlier editions or internal provenance are included.
    """
    doc = payload.get('native_document') or {}
    if not isinstance(doc, dict):
        return ()
    out = []
    patient = doc.get('patient') or {}
    identification = '; '.join(f'{k}: {patient[k]}' for k in ('name', 'dob', 'address') if patient.get(k))
    if identification:
        out.append(('Patient identity — date of birth / birthday', identification))
    for section in doc.get('sections', []):
        if isinstance(section, dict) and isinstance(section.get('text'), str):
            out.append((str(section.get('heading') or 'Document'), section['text']))
    for group in ('results', 'medications', 'prescriptions'):
        for row in doc.get(group, []):
            if isinstance(row, dict):
                text = '; '.join(f'{k.replace("_", " ")}: {v}' for k, v in row.items()
                                 if isinstance(v, (str, int, float, bool)) and k not in ('id','medicine_id'))
                if text:
                    out.append((group.title(), text))
    return tuple(out)
