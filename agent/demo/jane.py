"""An isolated, repeatable Jane Doe demo using original files and existing events."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import textwrap
import unicodedata

from .. import files, ingest, profile, projection
from ..config import CONFIG_FILENAME
from ..demo_metadata import SCENARIO
from ..errors import HealthAgentError
from ..events import envelope
from ..vault import Vault, scaffold
from . import DemoReport, MARKER_FILENAME, _identity, _require_empty, demo_config
from .authoring import claim, confirm
from .documents import page_pdf
from .jane_data import HISTORY

MARKER = '# Fictional Jane Doe demonstration record\n\nScenario: ' + SCENARIO + '''

All content is fictional demonstration data, not for clinical use. Names are
attributions within the story, not authenticated professional identities.
Sources are authored fixtures, not live AI extraction or clinician verification.
Only patient-visible summaries are included; no private clinician notes are used.
Voice-note sources are authored transcripts only: there are no audio recordings.
Collection, procedure and report-receipt notes are labelled where full reports
or numerical results are unavailable. This record needs no model download.
Re-running `health-agent demo --jane` on this folder fills missing seeded items;
it does not overwrite profile edits, source files, settings or user-added notes.
'''

PROFILE = {
    'full_name': 'Jane Doe', 'preferred_name': 'Jane', 'date_of_birth': '1935-11-01',
    'pronouns': 'she/her', 'sex_at_birth': 'Female', 'gender': 'Woman',
    'language': 'English', 'interpreter': 'No',
    'address': 'Example Gardens Residential Aged Care (fictional)\n1 Demonstration Lane\nSydney NSW 2000',
    'postal_address': 'Same as residential address',
    'home_phone': 'Fictional care reception: 02 5550 0100',
    'mobile': 'Not provided', 'email': 'jane.doe@example.invalid', 'preferred_contact': 'Other phone',
    'emergency_name': 'Emily Doe', 'emergency_relationship': 'Daughter; involved family carer, with Jane\'s permission',
    'emergency_phone': 'Fictional: 0400 000 000', 'emergency_other_phone': 'Not provided',
    'kin_name': 'John Doe', 'kin_relationship': 'Husband', 'kin_phone': 'Not provided',
    'kin_address': 'Not provided', 'gp_name': 'Dr James Smith', 'gp_practice': 'Sydney Aged Care (fictional)',
    'gp_phone': 'Fictional: 02 5550 0101', 'pharmacy': 'Harbour Clinical Pharmacy (fictional)',
    'pharmacy_phone': 'Fictional: 02 5550 0102',
    'accessibility': 'Usually walks to meals with a frame; manages eating independently (24 October 2024). Hearing aids fitted 3 September 2025; allow extra time to answer. Prefers plain-language explanations and written medication-change information. Recent changes and temporary assistance needs are attributed in the timeline, not a replacement for this usual baseline.',
    'advance_care': 'Advance directive and formal substitute decision-maker: Not provided. Emily is an involved daughter, not assumed to hold legal authority. Include her in discussions with Jane\'s permission.',
    'allergy_notes': 'Penicillin: Jane reports an itchy rash many years ago (recorded 28 October 2024). Timing and severity uncertain. Patient-reported; not clinician-verified.',
    'important_notes': 'FICTIONAL DEMONSTRATION DATA - not for clinical use. Residential aged care. Values meals, gardening and family visits. Cardiologist: Dr Matthew Thompson, Harbourside Cardiology (fictional). Facility nurse: Morgan Bell. Locum GPs: Dr Olivia Bennett and Dr Liam Wilson, Sydney Aged Care. Hypertension and knee osteoarthritis recorded 24 October 2024 (original diagnosis dates unknown); atrial fibrillation documented 2 May 2025. Current directions come from the released 28 August 2026 chart, reviewed 15 October 2026. Seeded transcription acceptance does not establish clinical verification. No live AI processing was used.',
    **{key: 'Not provided' for key in ('medicare_number','medicare_irn','ihi','dva_number','concession_number','private_fund','private_member_number')},
}

# Minimal invented directions for this scenario, always printed in the source.
BASE = {'Amlodipine': '2.5 mg once daily'}
AF = {**BASE, 'Apixaban': '5 mg twice daily', 'Metoprolol': '25 mg twice daily'}
STATIN = {**AF, 'Rosuvastatin': '5 mg nightly'}
HIGH_BP = {**STATIN, 'Amlodipine': '5 mg once daily'}
CHARTS = {'2024-10-24': BASE, '2025-01-15': BASE, '2025-05-06': AF,
          '2025-07-24': STATIN, '2026-08-20': HIGH_BP, '2026-08-28': STATIN,
          '2026-10-12': STATIN, '2026-10-15': STATIN}
RELATED = {
 '2024-11-20': ['2024-11-13'], '2025-04-11': ['2025-04-09'],
 '2025-05-02': ['2025-04-14','2025-04-24'], '2025-05-06': ['2025-05-02'],
 '2025-05-13': ['2025-05-06'], '2025-07-23': ['2025-07-16'],
 '2025-07-24': ['2025-07-23'], '2025-11-05': ['2025-11-02','2025-11-03'],
 '2025-11-07': ['2025-11-05'], '2026-02-12': ['2026-02-05'],
 '2026-06-16': ['2026-06-09'], '2026-07-30': ['2026-07-20'],
 '2026-08-20': ['2026-08-17'], '2026-08-26': ['2026-08-25','2026-08-20'],
 '2026-08-28': ['2026-08-26','2026-08-20'], '2026-10-12': ['2026-08-28','2026-09-24'],
 '2026-10-15': ['2026-10-08','2026-10-12'], '2026-10-19': ['2026-10-18'],
 '2026-10-21': ['2026-10-18','2026-10-19','2026-10-20'],
 '2026-10-22': ['2026-09-30','2026-10-21','2026-08-28'],
}


def source_text(item):
    lines = ['FICTIONAL DEMONSTRATION DOCUMENT - NOT FOR CLINICAL USE',
             item['source_type'], item['title'], '',
             'Patient: Jane Doe | DOB: 1 November 1935',
             'Example Gardens Residential Aged Care (fictional)',
             '1 Demonstration Lane, Sydney NSW 2000',
             'Event / document date: ' + item['date'],
             'Contributor / author: ' + item['author'],
             'To: Dr James Smith, Sydney Aged Care (where applicable)', '', item['text']]
    if item['source_type'] == 'Demo voice-note transcript':
        lines += ['', 'Authored fictional transcript only. No audio recording exists.',
                  'Original wording above is preserved. Not a live ASR transcript.']
    if item['date'] in CHARTS:
        lines += ['', 'RELEASED DEMO MEDICATION DIRECTIONS',
                  *[f'{name}: {dose}.' for name, dose in CHARTS[item['date']].items()],
                  'These are fictional directions, not treatment advice.',
                  'Penicillin rash reported by Jane; timing/severity uncertain;',
                  'patient-reported, not clinician-verified.']
    if item['date'] == '2024-10-24':
        lines += ['', 'Identity, family and practical care information',
                  *[f'{profile.FIELDS[k]["label"]}: {v}' for k,v in PROFILE.items()
                    if k not in ('important_notes','accessibility','allergy_notes')],
                  'Usual mobility: frame to meals; eating independently.',
                  'Care preferences recorded separately on 25 October 2024.']
    if item['date'] in ('2026-09-24','2026-10-08'):
        # Reuse only matching documents. The original bundled examples are untouched.
        import json
        library = Path(__file__).parents[1] / 'server/static_files/document-examples.json'
        docs = json.loads(library.read_text())['documents']
        doc_id = 'cardiology-2026-09-24' if item['date'] == '2026-09-24' else 'blood-panel-2026-10-08'
        doc = next(d for d in docs if d['id'] == doc_id)
        lines += ['', 'MATCHING ORIGINAL EXAMPLE CONTENT (fictional copy)']
        for section in doc['sections']:
            lines += [section['heading'], section['text']]
        for result in doc.get('results', []):
            lines += [f"{result['test']}: {result['result']} {result['unit']}"]
        lines += ['', 'Release metadata (outside the original correspondence):',
                  doc['release_note'], 'Demonstration workflow; not authenticated clinician approval.']
    if item['date'] in RELATED:
        lines += ['', 'Related stored demo sources (event dates):', ', '.join(RELATED[item['date']])]
    lines += ['', 'Patient-visible authored demo source. Not live AI extraction.',
              'Fictional names do not establish authenticated clinical identity.']
    return '\n'.join(lines)


def source_pdf(text):
    # Preserve original Unicode wording in a companion UTF-8 file as well. The
    # existing PDF writer supports ASCII only; typographic quotes are normalised.
    plain = text.translate(str.maketrans({'“':'"','”':'"','’':"'",'–':'-','—':'-', '×':'x', 'µ':'u', 'μ':'u', '⁹':'^9', '¹':'^1', '²':'2', '³':'^3'}))
    plain = unicodedata.normalize('NFKD', plain).encode('ascii','replace').decode()
    lines = [part for line in plain.splitlines() for part in (textwrap.wrap(line, width=78) or [''])]
    pages = [lines[i:i+43] for i in range(0,len(lines),43)]
    return page_pdf(pages, size=10, leading=15)


def seed(root, as_of=None, endpoint_from=None):
    root = Path(root).expanduser().resolve()
    marker = root / MARKER_FILENAME
    existing = root.exists() and any(root.iterdir()) if root.is_dir() else root.exists()
    if existing and (not marker.is_file() or marker.read_text() != MARKER):
        raise HealthAgentError('Jane demo population requires an empty folder or this exact Jane scenario. Existing ordinary records and other demos are never merged.')
    if endpoint_from:
        raise HealthAgentError('The preloaded Jane scenario needs no endpoint. Configure inference separately in Settings if wanted.')
    if not existing:
        _require_empty(root)
        scaffold(root)
        files.write_text(root / CONFIG_FILENAME, demo_config(), 0o644)
        files.write_text(marker, MARKER, 0o644)
    vault = Vault.open(root, identity=_identity())
    events = vault.read().events
    before = len(events)
    from ..projection.citations import index_artifacts
    from ..demo_metadata import entries
    artifacts = index_artifacts(events)
    seeded = entries(events, artifacts)
    references = {}
    from .jane_documents import native_document
    for item in HISTORY:
        native = native_document(item)
        text = source_text(item)
        pdf = source_pdf(text)
        previous = seeded.get(item['id'])
        if previous is not None and previous.payload.get('native_document', {}).get('release'):
            native['release'] = previous.payload['native_document']['release']
        if previous is not None:
            previous_short = previous.payload['artifact']
            # Version updates append a new source/note; immutable originals and
            # user additions remain untouched. An unchanged run writes nothing.
            if (artifacts[previous_short].digest == hashlib.sha256(pdf).hexdigest()
                    and all(previous.payload.get(k) == v for k,v in item.items())
                    and previous.payload.get('native_document') == native):
                references[item['date']] = previous_short
                continue
        result = ingest.ingest_bytes(vault, pdf, ingest.CaptureContext(
            source='import', original_filename=item['date'] + ' - ' + item['title'] + '.pdf',
            note='Authored fictional Jane demonstration source. Not AI extraction.'))
        # The raw UTF-8 original preserves typographic wording exactly, including
        # transcripts. It is a real original, not a phantom audio attachment.
        wording = ingest.ingest_bytes(vault, text.encode('utf-8'), ingest.CaptureContext(
            source='import', original_filename=item['date']+' - '+item['id']+' - original wording.txt',
            note='Original Unicode wording of fictional demo source. No audio exists.'))
        payload = {**item, 'demo_item_id': item['id'], 'demo_scenario': SCENARIO,
                   'fictional': True, 'patient_visible': True,
                   'event_date': item['date'], 'artifact': result.short,
                   'native_document': native,
                   'original_wording_artifact': wording.short,
                   'status': 'Authored fictional demonstration; not clinician-verified',
                   'related_dates': RELATED.get(item['date'], []),
                   'previous_artifacts': ([*previous.payload.get('previous_artifacts', []), previous.payload['artifact']]
                                          if previous else [])}
        vault.append(envelope.new('note.recorded', vault.identity.id, payload=payload))
        references[item['date']] = result.short
    # Only missing profile keys are filled on later runs. An explicit user clear
    # is an edit too: never reinstate seeded values once that key was written.
    touched = {k for e in events if e.type == profile.UPDATED for k in e.payload.get('values', {})}
    missing = {k:v for k,v in PROFILE.items() if k not in touched}
    current_profile=profile.read(events)['values']
    birthday_migrated=any(e.payload.get('demo_calendar_key')=='birthday-november-v1' for e in events)
    if not birthday_migrated and current_profile['date_of_birth'] != PROFILE['date_of_birth']:
        missing['date_of_birth']=PROFILE['date_of_birth']
    prior_seeded_note=next((e.payload.get('values',{}).get('important_notes') for e in reversed(events)
                            if e.type==profile.UPDATED and e.payload.get('demo_scenario')==SCENARIO
                            and 'important_notes' in e.payload.get('values',{})),None)
    if prior_seeded_note is not None and current_profile['important_notes']==prior_seeded_note:
        if current_profile['important_notes']!=PROFILE['important_notes']:
            missing['important_notes']=PROFILE['important_notes']
    if missing:
        profile.validate(missing, datetime.now(timezone.utc).date())
        vault.append(envelope.new(profile.UPDATED, vault.identity.id,
            payload={'values': missing, 'demo_scenario': SCENARIO}))
    seeded_claims = {e.payload.get('demo_claim_id') for e in events if e.payload.get('demo_scenario') == SCENARIO}
    # Clinical entries use the original claim/review architecture, with explicit
    # fixture provenance. Acceptance represents transcription review, not clinical
    # verification. Dates on the documents govern historical supersession.
    claims = []
    for day, chart in CHARTS.items():
        if day in ('2025-01-15','2026-10-12'):  # repeats, not new prescription changes
            continue
        for name, dose in chart.items():
            claims.append((day, 'med:'+name.lower(), 'dose', dose, name))
    claims += [('2024-10-24','problem:hypertension','name','Hypertension','Hypertension'),
               ('2024-10-24','problem:knee-osteoarthritis','name','Knee osteoarthritis','Knee osteoarthritis'),
               ('2025-05-02','problem:atrial-fibrillation','name','Atrial fibrillation','Atrial fibrillation')]
    for day, subject, predicate, value, name in claims:
        key = day+':'+subject+':'+predicate
        if key in seeded_claims:
            continue
        short = references[day]
        stamp = day+'T12:00:00Z'
        proposed = claim(vault.identity.id, subject, predicate, value,
            artifact=short, ts=envelope.now_ts(), occurred={'value':day,'precision':'day','uncertainty_days':0},
            artifact_ts=stamp, subject_name=name, demo_scenario=SCENARIO, demo_claim_id=key,
            note='Authored demo transcription; not clinician verification or live AI.')
        proposed = replace(proposed, provenance={'model':'authored-fictional-demo',
            'model_rev':'no-model-run','prompt_hash':'not-applicable','artifact':short})
        vault.append(proposed)
        vault.append(confirm(vault.identity.id, proposed.id))
    from . import calendar_data, progress
    calendar_data.seed(vault)
    progress.seed(vault)
    result = projection.rebuild(vault, as_of=as_of)
    return DemoReport(root=root, artifacts=len(index_artifacts(vault.read().events)),
                      events=len(vault.read().events)-before, rebuild=result)
