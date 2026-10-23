# Jane Doe's fictional two-year record

This is authored fictional demonstration data, not for clinical use. It is
separate from ordinary records and needs no AI model or download to browse.

On this Mac, from the repository directory:

```sh
.venv314/bin/health-agent demo ./vault/jane-doe-demo --jane --json
.venv314/bin/health-agent app --vault ./vault/jane-doe-demo --no-tray
```

If an older app is still running, stop it with Control-C in its terminal first.
Open http://127.0.0.1:7777/overview. The header identifies the fictional Jane Doe
demonstration. The explicit `--vault` opens the demo without changing your
remembered ordinary record. Omit `--vault` on a later app launch to return to it.
The generated folder is ignored by Git. Keep it if you add your own demo notes.

The population command is safe to repeat on this scenario. It fills missing
items and appends versions of changed authored fixtures, preserving originals.
It does not overwrite profile edits, including explicitly cleared fields,
user-created notes/uploads, settings, or model files. It refuses non-empty
ordinary vaults and unrelated demos. The original generic `demo PATH` still
works as before; `--jane` selects this dated scenario.

## What to open

- Overview shows the latest five entries from `/api/timeline?limit=5`.
- Timeline shows all 68 events, 24 October 2024 through 22 October 2026,
  newest first. Its existing date filters, source filters and calendar use the
  same projection. Click an event or its source citation to open the original.
- Documents opens the record's own collection in this scenario. Search by title,
  author or date and filter by source type. The separate illustrative example
  library remains available; it is not silently merged into this record.
- Profile contains Jane's identity, residential-care setting, family/care team,
  preferences, usual mobility/communication needs and reported penicillin rash.
  Unavailable official identifiers remain "Not provided". Clinical conditions
  and medication information retain source citations and historical entries.

Useful journeys:

1. 18 October 2026, **Carer observation - increased walking assistance** →
   Emily's source note → original text wording.
2. 28 August 2026, **Revised medication chart received** → current chart →
   related 20 August prescription and 26 August assessment. Older directions
   remain historical; the latest supported directions govern the current list.
3. 8 October 2026, **Blood panel, including lipids, received** → the matching
   copied fictional report. The 15 October results review also links this source.

Every event has a readable stored PDF and a UTF-8 text original. Personal and
carer wording is preserved in the text original; PDFs normalise typography.
Voice-note entries are explicitly fictional transcripts and have no audio player.
Event/document dates are separate from the actual date the source was added.
Sources identify the contributor and authored-demo status. Names and release
wording do not authenticate a clinician or establish clinical verification.

## Scope and verification

Collection/procedure/report-receipt notes explicitly state where full reports
or numerical results are unavailable. This includes the July FBE receipt and
echocardiogram completion: these are not fabricated full reports. Only matching
September cardiology and October pathology library documents are copied; the
contradictory generic medication review is preserved in the example library and
has a separate scenario-specific counterpart here.

Preloaded demo originals are excluded from automatic extraction queues, while
new user uploads retain normal extraction behaviour. Asking the existing AI or
processing new uploads still needs the existing integrations; the dataset does
not simulate those integrations. Sealed Doctor's notes are not opened or used.

Run focused checks with:

```sh
.venv314/bin/python -m unittest tests.test_jane_demo tests.test_patient_profile tests.test_overview tests.test_documents -v
.venv314/bin/health-agent check --vault ./vault/jane-doe-demo --json
```

Checks cover all 68 dates and sources, profile consistency, medication history,
related links, filters/calendar queries, original wording, no fake audio,
private-note exclusion, source lookup/security boundaries, demo isolation,
repeat population and preserved edits, deterministic replay, PDF text bounds,
served JavaScript syntax, and native Overview rendering. Existing document and
profile checks also run. Representative PDFs were rendered and visually checked.

Full visual desktop/mobile browser inspection and an npm/TypeScript build were
not available in the execution environment. FastAPI pages and assets were tested
through its in-process HTTP client; binding a local server port was blocked by
the execution sandbox. The shipped JavaScript includes the corresponding small
source-view and timeline-link changes so this Mac does not need a frontend build.

## Native reading pages and prescription details

Document links now open a readable page inside the app using the existing
letter template. Full native editions are stored in the demo's append-only
notes; PDFs and UTF-8 originals remain unchanged as secondary downloads.
The native edition is identified separately in optional provenance details.
Personal and carer wording is preserved. Pathology uses the recorded results,
with reference intervals explicitly unknown where the source supplies none.
Text-layer uploads can also show extracted text; scanned, unreadable or mixed
pages without reliable text retain their original-file viewer. No OCR or AI is
invented for this view.

Document rows and reading pages show the recorded releasing person and date,
separately from author and recipient. Demo releases are simulated metadata, not
an authentication or permission system. Patient/carer entries show their named
contributor instead. Existing recorded release identities are preserved on
repopulation; ordinary records receive no release grants.

Open **Health record → Amlodipine** to compare the current and superseded
prescriptions. The source chart also displays its full prescription fields:

- Current 28 August 2026 prescription: amlodipine 2.5 mg once daily, 30 tablets,
  originally two repeats. The 15 October shared GP record explicitly records
  zero repeats remaining. Expiry is 31 October 2026.
- Historical 20 August 2026 prescription: amlodipine 5 mg once daily, superseded
  by the 28 August chart. Its recorded expiry is 12 October 2026.
- The current apixaban, metoprolol and rosuvastatin records have later recorded
  expiry dates. No undocumented repeat balance or medicine supply is inferred.

Expiry badges use Australia/Sydney calendar days at request time. The expiry
day itself is blue; it becomes red the following day. Today through 30 days is
blue, more than 30 days uses normal styling, and unknown expiry is neutral.
Expiry does not stop treatment, and repeat balance is independent of expiry.
These dates are fixed fictional prescription data, not rolled forward to make
screenshots retain a particular colour. As time passes, the badges change.

The medication list shows the current prescription badge; medication detail
pages show the fuller current and historical records. Source pages and the
appointment sheet use the same recorded prescription data. Original directions
remain visible, with no conversion of "twice daily" to undocumented clock times.
Missing fields read "Not recorded". Supply is not estimated from repeats or PRN
maximums.

Additional focused checks:

```sh
.venv314/bin/python -m unittest tests.test_native_documents -v
```

The full focused set contains 33 tests. Native page rendering, escaping,
release attribution, stable originals, prescription/source consistency,
Sydney midnight boundaries, repeat balances, unknown/PRN fields, and uploaded
PDF text views are covered. Desktop/mobile visual browser testing and the
TypeScript build remain unverified in the restricted execution environment.
