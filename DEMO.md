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
- The original timeline contains 68 events, 24 October 2024 through 22 October 2026,
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

## Calendar and facility progress notes

Open **Calendar** (`/calendar`) for the month view and upcoming list. Select a
day to see its events; select an event for its time, professional, location and
supporting record where available. **Add event** saves an appointment or other
event in the current vault. It does not clutter the health timeline with future
bookings. Times use Australia/Sydney; invalid dates and nonexistent daylight
saving times are rejected. Retrying a save does not create a second entry.

Jane's corrected DOB is **1 November 1935**, so her annual birthday marker is
1 November. This task's birthday migration runs once; later manual profile
changes are preserved. Current native editions show the corrected DOB. Older
source files remain immutable and accessible through their original links.
The optional native provenance details explain the correction.

The fictional calendar includes past appointments drawn from the same stored
visit records, facility locum visits, and bookings for podiatry (28 October
2026, 10:30 am), GP review (3 November), physiotherapy, audiology, cardiology
and a care-plan review. Prescription expiry events use the recorded dates and
link to the prescription source; expiry is not a treatment-stop instruction.
Bookings do not automatically become "attended" as time passes.

**Progress notes** opens within the authenticated clinician workspace without a
second password. All 21 fictional notes retain their original authors, dates and
text. New notes record the signed-in clinician's identity. Notes remain excluded
from patient views, summaries, answers and document release.

Notes use server-managed AES-GCM encryption at rest. The private machine key lives
outside the vault, under the application configuration's `clinician-note-keys`
directory, and must be retained with machine backups. This is not end-to-end
encryption: the authenticated server decrypts notes for authorised clinicians.
The original legacy ciphertext remains recoverable. The known demo collection
was imported once; other legacy collections offer a one-time import using their
original passphrase rather than resetting or discarding their contents.

Focused checks: `tests/test_progress_notes_session.py` and
`frontend/tools/check-session-progress-notes.mjs` cover session protection,
encrypted storage, original-note preservation and desktop/mobile access.


## Optional OpenAI connection

Restart the application, open Settings, select **Read on another computer**,
then **Use OpenAI API**. Store your OpenAI API key in the Password field,
click Connect, choose a model that supports images and JSON output, and Connect
again. Successful connection requires both the structured-response and image tests
to pass. This uses `https://api.openai.com/v1` and sends submitted content to OpenAI;
API billing is separate from ChatGPT. Preloaded demo content still needs no model.
Keys stay outside the record folder and are isolated from private-server keys.
Never put a key in chat, source code or the vault's config.toml.

Provider tests: `.venv314/bin/python -m unittest tests.test_openai_provider -v`.
These use mocked responses and do not establish live account access or billing.

## Post-consultation dashboard continuation

The original 68-source history ends on 22 October 2026. The carer/nurse/patient
observations on 18–20 October precede Dr Smith's 21 October consultation; they
correctly do not appear in an exclusive comparison after that date. A previously
planned later demonstration sequence was not present in the repository or vault.

The dashboard correction appends three clearly authored fictional sources:
23 October (Jane, breakfast participation), 24 October (Emily, walking support),
and 25 October (Morgan, nearby staff support). These extend the established
scenario without changing original dates, diagnoses, prescriptions, historical
source files, user-added notes or sealed clinical notes. Their PDF/text originals
and native metadata explicitly identify their newly authored demo origin.

```sh
.venv314/bin/python -m agent.demo.review_sequence --vault ./vault/jane-doe-demo
```

This explicit, idempotent command adds missing continuation sources only. The
original two-year scenario and its original manifest remain intact; the
continuation brings the authored source count to 71. It creates no clinical
claims, review-completion events or tasks. Dashboard summary notices distinguish
prepared demo content from later ordinary typed notes; those notes retain their
original event text and recorded timestamp, and an unknown author/date of
observation is not inferred.

Outstanding follow-up now uses the explicit unresolved 22 October heart-medication
question, not calendar bookings. The nearest three future appointments have their
own compact section; past bookings without attendance evidence say “Attendance
unknown”. Filtering uses source-event dates, except original typed notes whose
only known date is explicitly labelled “date recorded”.

## Revised clinician review demonstration (review-v2)

The local Jane record is replaced only through `agent.demo.review_dataset.refresh`:
first a complete recoverable copy is made beside the active vault, then a new
staging vault is populated and swapped into place. Other vaults are not touched.
The seeder refuses to reset an already refreshed vault, so it cannot silently
wipe review work. Stop the app before restoring an archive to the active path.

The refreshed record has 14 clinical PDF originals across November 2024–October
2026, plus five original patient/carer/staff updates. Its `DATASET-MANIFEST.json`
records source IDs, original hashes, fictional event/document/receipt dates,
actual import dates and demonstration purposes. These are newly authored fiction,
not recovered historical reports or live generation. The sodium critical alert is
an explicit fictional laboratory flag. It does not establish a cause for symptoms
or a clinician-assigned priority. Apixaban remains on the current chart; there is
no warfarin/INR monitoring scenario.

Review, priority assessments, communications, releases, appointment requests,
internal clinical actions and explicit action resolutions are independent
append-only `note.recorded` workflow events. Authenticated clinician identity and
server timestamps are recorded. Source opening changes no review state. A review
covers explicitly identified contributions, not any later contribution silently.
The two clinicians share item review/audit history but keep their own documented
consultation boundaries. Semantic deduplication and submission IDs prevent repeated
clicks from creating duplicate requests, messages or releases.

The original laboratory flag is retained after a clinician changes their priority.
Explicit source-critical flags sort first, followed by clinician priority and
recency. Related walking reports and Jane's linked dizziness/meal-participation
reports retain individual source text. Date filtering happens before grouping;
older pathology is labelled context and excluded from current-period source counts.
Unresolved clinician-created actions keep their dated source visible outside the
selected period. Ordinary calendar bookings do not create actions.

Patient/carer notification and release use a **shared, unauthenticated demonstration
workspace**. Jane explicitly authorises Emily for these fictional communications.
Recipients are recorded and shown, but access separation between Jane and Emily
is simulated; this is not individual carer authentication. Acknowledgements are
explicit demo actions, never inferred from sending or reading a screen, and never
prove understanding. Phone/in-person records are clinician-entered attestations.
No email, SMS, external scheduling or real delivery service is implemented.
Appointment requests are outstanding actions and in-app notifications, not bookings.
Resolving their action records does not fabricate a calendar booking.

Only a reviewed, selected clinical document and its approved message can be
released. Patient API projections and original-file/metadata endpoints exclude
unreleased clinical sources; raw vault browsing uses protected clinician endpoints.
Clinician uploads use protected capture endpoints in this refreshed scenario.
Patient/carer original submissions stay accessible without release. Sealed clinical
notes require clinician authentication and remain encrypted at rest;
they are not release candidates. No existing original is overwritten by workflow.

Automated browser mutations run against the disposable fixture on port 7788 via
`frontend/tools/serve-review-fixture.py` and `check-review-workflow.mjs`. The active
port-7777 demo is checked read-only and begins with recent items unreviewed,
unnotified and unreleased. Supply the fixture password through the environment;
credentials are never rendered in the interface or stored as plaintext in the repo.

## Expanded specialist correspondence

`python -m agent.demo.specialist_letters --vault ./vault/jane-doe-demo` appends an
expanded fictional cardiology edition and new podiatry/audiology correspondence.
It is idempotent and requires these authors to be existing care-team members.
The new documents are dated when authored; the original cardiology PDF and its
historical document/receipt dates remain retained. The new edition requires fresh
review and release, while earlier audit records remain intact. Prescription
content restates the current chart and does not issue a new medication change.

## Clinician structured record

The clinician Clinical record uses four tabs and a table/detail layout. Its
values come from the existing entity projection and original source metadata.
First-documented dates are the earliest supporting source dates available, not
inferred onset dates. Medicine relationships are shown only for explicitly scoped
prescription records. Unknown routes, prescription validity, supply and functional
findings remain unknown.

Authorised Add/Edit actions append `claim.corrected` events and an internal
`clinical_record_edit` audit containing account identity, timestamp, previous
values and stated evidence. Functional edits also update the existing clinician
profile accessibility summary. Concurrent stale edits are rejected. Original
files, consultation comparison dates and document release records are unchanged.
These structured edits do not issue prescriptions or release clinical information
to the patient workspace. Existing patient-source permissions still apply.

Checks: `tests/test_clinical_record.py` and
`frontend/tools/check-clinical-record.mjs`. Browser writes require
`MUTATE_FIXTURE=1` and the isolated test server on port 7788.
