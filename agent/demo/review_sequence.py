"""Explicitly authored fictional continuation, added by the dashboard correction.

The original 18–22 October sequence is retained unchanged. These are new demo
sources, not recovered originals, inferred clinical observations or live AI output.
"""
from pathlib import Path
from .. import ingest
from ..events import envelope
from ..demo_metadata import SCENARIO, entries
from ..vault import Vault
from ..server.state import RecordState
from .jane import source_text, source_pdf
from .jane_documents import native_document

ORIGIN = 'Authored fictional continuation added for clinician-dashboard demonstration; not a recovered original or clinical verification.'
SOURCES = [
    {'id':'jane-review-patient-2026-10-23', 'date':'2026-10-23', 'title':'Patient update - breakfast participation',
     'author':'Jane Doe', 'source_type':'Authored fictional patient follow-up note', 'event_type':'observation',
     'text':'Jane reports that she is still staying in her room for breakfast because she worries about getting dizzy. She says she misses seeing the other residents in the dining room.'},
    {'id':'jane-review-carer-2026-10-24', 'date':'2026-10-24', 'title':'Carer update - walking support',
     'author':'Emily Doe, daughter', 'source_type':'Authored fictional carer follow-up note', 'event_type':'observation',
     'text':'Emily reports that Jane asked her to stay beside her on a walk to the dining room and paused to rest. Emily describes the effect on Jane’s usual routine.'},
    {'id':'jane-review-staff-2026-10-25', 'date':'2026-10-25', 'title':'Staff observation - nearby support when walking',
     'author':'Morgan Bell, registered nurse', 'source_type':'Patient-visible nursing communication', 'event_type':'observation',
     'text':'Morgan records that Jane asked staff to stay nearby when walking towards the dining room with her frame. The note also records Jane’s reported worry about dizziness.'},
]


def seed(root):
    vault = root if isinstance(root, Vault) else Vault.open(Path(root))
    snapshot = RecordState(vault).snapshot()
    original = entries(snapshot.events, snapshot.artifacts)
    if not original or not (vault.root/'DEMO-DATA.md').is_file():
        raise ValueError('This continuation may only be added to the existing Jane demonstration record.')
    added = 0
    for item in SOURCES:
        # Stable IDs make a repeat append nothing. Never replace an earlier source.
        if item['id'] in original:
            continue
        text = source_text(item) + '\n\nDemo origin: ' + ORIGIN
        native = native_document(item)
        native['sections'].append({'heading':'Demo origin', 'text':ORIGIN})
        native['release'] = None
        pdf = ingest.ingest_bytes(vault, source_pdf(text), ingest.CaptureContext(
            source='import', original_filename=item['date']+' - '+item['title']+'.pdf', note=ORIGIN))
        wording = ingest.ingest_bytes(vault, text.encode(), ingest.CaptureContext(
            source='import', original_filename=item['date']+' - '+item['title']+'.txt', note=ORIGIN))
        vault.append(envelope.new('note.recorded',vault.identity.id,payload={
            **item, 'demo_item_id':item['id'], 'demo_scenario':SCENARIO,
            'fictional':True, 'patient_visible':True, 'demo_origin':ORIGIN,
            'event_date':item['date'], 'artifact':pdf.short,
            'original_wording_artifact':wording.short, 'native_document':native,
            'status':'Authored fictional continuation; not clinician-verified'}))
        added += 1
    return added


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True)
    args = parser.parse_args()
    print(f'Added {seed(args.vault)} fictional source entries; existing history preserved.')
