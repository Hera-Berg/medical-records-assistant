# The interface

Vite + React + TypeScript + Tailwind, built to static files and served by
FastAPI at `/`. One origin, no dev proxy in production, no CDN, no webfont.

The opening route is Overview (`/` or `/overview`); the patient profile lives at
`/profile` and questions at `/ask`. The overview reads `/api/profile` and the
recent timeline, with no model call or record writes. Profile
details are user edits stored as `patient.profile.updated` events, served by
`/api/profile`. Confirmed clinical entries come from the reconciled record;
patient-entered allergy notes are labelled separately. The native profile
editor, Overview, Documents and appearance control live in `public/` as shared browser assets,
copied unchanged by Vite. React mounts their custom elements in the relevant
screens. Keep their shipped copies in `agent/server/static_files/` in sync.

Documents (`/documents`) separates the bundled fictional Jane Doe examples in
`public/document-examples.json` from the user's uploads (`/api/documents`).
Examples never enter a vault or its clinical projection. Their dates and
sharing statements apply only to the examples. The original folder browser
remains accessible at `/files` and through Documents → Browse record folder.

## The build is committed

`npm run build` writes to `../agent/server/static_files`, and **that output is
committed to the repository.**

This is deliberate and it is the reason there is no build step in the install
instructions. The project is meant to be self-hosted by someone who has Python
and nothing else; a Vite build standing between `pip install` and a working app
puts a node toolchain into the install story of a personal health record, which
is a real barrier for the person this is for. So:

```
pip install -e .
health-agent serve
```

works on a machine with no node installed at all.

Building the interface is a **developer** step. If you are only using the app,
you never run anything in this directory.

## Working on it

```
npm ci                 # once
npm run dev            # vite dev server, proxying /api to 127.0.0.1:7777
npm run build          # typecheck, bundle, and write the committed output
npm run check          # typecheck only
```

`npm run dev` needs `health-agent serve` running for the API. Development is the
only place two origins exist; production is one.

**Commit the rebuilt `static_files` with your change.** Every build stamps
`build-info.json` with the commit it came from, and `/api/build` serves that
back — so a bundle that has drifted from its source can be identified from a
running server rather than by reading the interface and wondering. A build made
from a dirty working tree records `dirty: true`, because a bundle that cannot be
traced to a commit should say so.

## Reading the output

There is no browser in some environments, and reading the components is not the
same as reading what they render. `tools/render.mjs` mounts the real bundle into
a DOM against a running server and prints the result as text:

```
health-agent serve --vault /path/to/vault &
node tools/render.mjs / /record /record/med:perindopril /artifact/a3f91c
```

Use it on the awkward shapes, not only the happy path: a conflicted entity, a
stale medication, an empty timeline, a merge stub, an artefact whose bytes are
missing. Every defect found in phase 5's interface was found this way and none
of them was visible from a passing test suite.

## Photographing the recorder

Reading text is not enough for the recorder, because most of its states are ones
a developer with a working microphone never reaches: permission refused, no
input device, a page opened at a LAN address where `getUserMedia` silently does
nothing. `tools/shots.py` drives a real Chromium against a real server and
photographs each one.

```
health-agent serve --vault /path/to/vault &
python frontend/tools/shots.py --out /tmp/shots
```

Needs `pip install playwright` and `python -m playwright install chromium` —
neither is a dependency of the application.

Nothing in it is mocked. The "recording" shot is a browser recording a
synthesised voice note through Chromium's fake audio device, and the transcript
in the shot after it is what `faster-whisper` made of the file that browser
uploaded. A screenshot of a mocked state is a picture of the mock.

The one state that needs arranging is **waiting to be typed up**. It is real,
but on a laptop it lasts about a second, so:

```
health-agent serve --vault /path/to/vault --port 7788 --no-worker &
python frontend/tools/shots.py --origin http://127.0.0.1:7788 --waiting-only
```

A server that is not draining holds exactly that state, which is what a deep
queue looks like. Without `--waiting-only` the script watches for the state and
says it passed too quickly rather than posing it.

## Photographing the inference endpoint

Connect exists to tell apart states a developer on a working laptop never
reaches: a box that refuses the key, one that answers text perfectly and
silently throws every picture away, one that ignores the schema it was handed,
one that offers four models and has to be asked which. Those are exactly the
states where a person is stuck and reading the screen carefully, so they are
photographed — including the two that are folded shut by default, because a
disclosure nobody opens is a disclosure whose contents nobody has read.

```
python frontend/tools/shots.py --endpoint-only --out /tmp/shots
```

This is the one mode that does **not** want a server already running. It builds
the whole arrangement itself in a temporary directory and tears it down
afterwards:

- a throwaway demo vault, which carries no endpoint — that is the "nothing set
  up yet" shot, and it is real rather than posed;
- `HEALTH_AGENT_CONFIG_HOME` pointed at a temporary directory, so **nothing
  touches your keychain, your device identity or any real vault**. The
  credential the app finds is a throwaway one written into a 0600 file there.
  The settings screen's own password field writes to the OS keychain, and
  overwriting the key you actually use in order to take a photograph is not a
  trade worth making — that path is covered by tests instead;
- `tools/fake_box.py`, a real OpenAI-compatible HTTP server on loopback with a
  mode switch, so each failure is a machine genuinely behaving that way.

Nothing between the browser and that box is stubbed. The address guard resolves
the address for real, the client sends real requests, the probe reads real
answers — including the vision check, which really does send a generated image
with known text in it and really does look for that text in the reply. The
unreachable shot is taken by closing the box's socket, not by pretending.

The fake box can also be run on its own while working on the screen:

```
python frontend/tools/fake_box.py --port 7999 --mode blind
```

`working`, `blind`, `unconstrained`, `unauthorised` and `mismatched`. It is a
developer tool, imported by nothing under `agent/`, and it speaks only the two
paths the client uses.

## Constraints

These are not style preferences. Each has a reason, and they are enforced by
review rather than by a linter.

- **Shared type scale.** Use the system sans-serif stack. Page titles are 26px
  (24px on smaller screens), section headings 17px and clinician body text 15px.
  Patient workspace body text stays at 16px. Metadata and control labels may be
  smaller when secondary to the main content.
- **Colour carries no meaning on its own.** Every evidence tier, conflict and
  staleness state has a label as well as a hue. A colour-blind reader and a
  sheet of paper must both work — the consultation summary exists to be printed
  and handed to a clinician.
- **The label is the word the patient would use.** "Prescription", not `RX`;
  "Needs confirming", not `STALE`; "6 August 2026", not `2026-08-06`; the kind
  of document a citation points at, not its six-character hash. The identifiers
  are the record's filing system and they belong in the folder, on the CLI and
  on an entity's own detail page — not in the columns someone reads to find out
  what they are taking. A status word that needs a legend is a status word the
  owner of the record cannot use.
- **No shadows and no gradients.** A panel is a hairline rule and a change of
  ground. Softness comes from space and corner radius, both of which a printer
  can reproduce; a drop shadow prints as nothing or as grey mud, and this page
  is printed.
- **No animation.** Nothing may delay reading.
- **Tables for anything list-shaped.** A card grid is slower to scan and takes
  more room.
- **System fonts only, and no network call the page was not asked to make.** A
  webfont is a network call. The content security policy on the served document
  enforces this in the browser rather than trusting the bundler config.

## Measuring the consultation sheet

The sheet has a hard one-page limit, and `agent/summary/budget.py` enforces it
from an arithmetic model of the page. **That model cannot be derived; it has to
be calibrated.** A table cell's line box is set by the strut of its own type, so
a note in smaller text still costs a full line — the first version of the budget
assumed otherwise and was wrong by a whole page.

```
health-agent serve --vault /path/to/vault &
python frontend/tools/sheet.py --out /tmp/sheet --pdf
```

For every summary in the record it renders `/print/{id}` through a real browser,
prints it to an A4 PDF, and reports the page count, the measured height against
the budget's estimate, and the height of every section and every row. Re-run it
after any change to `agent/summary/html.py`, and change the constants in
`agent/summary/model.py` rather than the assertion.

The model deliberately reads 10 to 15mm long, because it adds every margin where
a browser collapses adjacent ones. That is the direction to be wrong in: an
over-estimate costs a row, and an under-estimate is a sheet that says it fits on
one page and does not.

**Print one.** A PDF answers "does it fit"; only paper answers "is this a thing
a person would read in a waiting room", and paper is how this document actually
reaches a clinician.

## Demo workspaces

The header's **Demo workspace switch** opens the patient & carer homepage at
`/overview` (also `/`) or clinician sign-in/dashboard at `/clinician`. Shared
record routes keep the workspace preference in browser local storage; opening
one of the home routes explicitly selects its workspace. This preference never grants access: clinician pages require server-verified
sign-in, and encrypted progress notes still require their separate passphrase.

After building, check navigation against a running demo server:

```sh
HEALTH_ORIGIN=http://127.0.0.1:7788 node tools/check-workspaces.mjs
```

This checks the rendered DOM, switching, shared routes, stored preference,
browser Back and unavailable local storage. It does not check browser layout.

Desktop navigation stays visible in both workspaces; smaller screens use a
collapsible menu. Clinicians have Review dashboard, Clinical record, Documents,
Timeline and Clinical notes. Patients/carers have Home, Record an update,
Calendar, Shared documents and My care team. `/care-team` renders the complete current care-team grid from the same
`/api/profile` care-team data used by the profile and overview. Other record tools, profile
and settings remain in secondary navigation. Clinical notes are omitted from
patient/carer navigation; their existing encrypted, passphrase-protected routes
are preserved. “Shared documents” is a navigation label for the existing document
collection, not a new sharing or permissions system.

## Shared design foundation

`src/styles.css` owns the palette, type scale, spacing and shell geometry.
`public/appearance.css` supplies the matching dark theme. Page styles must not
change root palette tokens or shell branding. Light mode uses a neutral grey
application background, white surfaces, dark text and purple for actions/selection.
Warning/error hues are reserved for labelled status, never ordinary information.

The sidebar is 232px and the desktop header 64px. Small screens wrap header
controls and collapse navigation. Use `.surface`/`.panel`, `.section-heading`,
`.metadata`, `.data-list`/`.data-row`, `.badge`, `.btn`/`.btn-primary`,
`.tabs`/`.tab` and `.source-link` for future features. Tabs need the appropriate
ARIA state and keyboard behaviour when implemented; these classes supply styling
only. Panels use 8px corners; controls use 6px. Keyboard focus remains visible.

The working title is “Care record”. The clinician route requires server-verified sign-in and opens a compact,
source-linked review dashboard. Technical storage and reader status are under
Settings → Advanced settings. The workspace switch remains a demonstration
control and never authorises access to clinical notes.

## Care-team membership and portraits

`agent/care_team.py` reads explicit roster entries and documented allied-health
follow-up bookings, then enriches existing members with matching native source
documents. Historical authorship alone does not establish membership. Profile GP
and pharmacy fields remain editable through the existing profile workflow. An
explicitly identified family carer appears separately; next-of-kin status alone
does not establish a care role. This projection never reads sealed notes or
writes to the vault.

Jane's grid includes the four facility members, cardiologist, physiotherapist,
audiologist, podiatrist, pharmacist, pharmacy and involved daughter Emily.
`public/assets/care-team/` contains only the three user-supplied photographs,
copied without modification. The page uses CSS face crops, marks each photograph
as illustrative, and uses neutral initials/organisation frames for everyone else.
Photo mappings apply only to the Jane demo scenario, never ordinary records.

## Clinician authentication and dashboard

Clinician accounts are explicitly provisioned for the Jane development demo:

```sh
.venv314/bin/python -m agent.clinician_auth provision-demo --vault ./vault/jane-doe-demo
```

The command prints new random passwords once. Do not put that output in the
repository or application UI. It refuses existing account files and ordinary
records. Only salted PBKDF2-SHA256 hashes (600,000 iterations) are stored, in the
machine config directory's `clinician-auth/` directory, scoped to the vault's
resolved path. Nothing is written to Jane's clinical event history. Test setups
can set `HEALTH_CLINICIAN_AUTH_DIR` to an isolated temporary directory.

Sessions use random opaque tokens in host-only HttpOnly/SameSite=Strict cookies,
with token hashes held on the server. Tokens rotate on login; sign-out revokes
them. Idle expiry is 30 minutes, absolute expiry eight hours, and server restart
invalidates all sessions. Unsafe clinical-note requests and sign-out require a
per-session CSRF token and exact same-origin checks. Five failed login attempts
per client address trigger a 15-minute cooldown. Identity and roles come from
the verified account, never local-storage workspace preferences.

`/clinician` and `/clinician/*` redirect signed-out visitors to
`/clinician/sign-in`. `/api/clinician/*` and `/api/doctor-notes*` reject unauthenticated
requests with 401. Read-only clinician aliases reuse the existing record/profile/
calendar/document handlers. Original patient-visible `/api/profile`, documents,
timeline and record APIs intentionally remain accessible through the existing
patient flow; patient authentication is outside this task. Clinician-only notes
remain encrypted and need their separate passphrase even after account sign-in.

The service still binds to loopback only. On its existing HTTP loopback origin,
the cookie cannot provide TLS transport encryption; the Secure cookie flag is
set when served over HTTPS. This is local development authentication, not a
production multi-user deployment or protection against someone who can read
local files. Production use needs HTTPS, deployment hardening and independent
security review. Accounts/sessions are machine-local and do not sync with the
patient vault.

The dashboard is a read-only projection of Jane’s authored demo sources,
original typed contributions and explicit bookings. The default comparison is the authenticated
clinician's latest completed/attended consultation (James: 21 October 2026;
Olivia: 26 March 2025). A clinician without one must select a comparison date.
No generated-summary timestamp becomes a consultation date. Prepared source
excerpts are labelled as awaiting clinician review; there is no live generation,
new review-completion state, task-management feature or other-patient inbox.
Source-event dates determine the comparison interval; outstanding follow-up
contains only explicitly documented unresolved requests. Ordinary appointments
are separate; attendance is unknown where completion is not documented.

Verification: `python -m unittest tests.test_clinician_auth -q` covers password
checks, access boundaries, account separation, source links, expiry/revocation,
CSRF, assignment checks and the first-review path. Browser verification should
also exercise both sign-ins, refresh, sign-out, the locked notes UI and responsive
layout. Demo credentials must be supplied through the environment to any browser
test runner, never checked into test code.

The browser check is `frontend/tools/check-clinician.mjs`. Supply
`CARE_JAMES_PASSWORD` and `CARE_OLIVIA_PASSWORD` locally, and optionally
`HEALTH_ORIGIN`, `PLAYWRIGHT_MODULE` and `CHROME_PATH`. It requires developer-only
Playwright and Chrome; neither is an application dependency. Older workspace
checks that assume anonymous clinician access are superseded by this check.

## Dashboard correction: source windows and appointment semantics

The original 18–20 October patient/carer/nurse observations precede Dr Smith’s
21 October consultation and are excluded by a strict “after” comparison. The
separate, explicit continuation command documented in `DEMO.md` appends three
newly authored fictional sources for 23–25 October; it never changes historical
dates or modifies clinical claims. Later ordinary typed notes are also included
with their recorded dates, unknown author where no author was recorded, and a
protected original-event view at `/clinician/observation/{event_id}`. Sealed notes
are never eligible for that endpoint or the dashboard.

Only the explicit unresolved 22 October medication question currently qualifies
as outstanding follow-up. Bookings do not create clinical actions. The nearest
three future appointments are separate; past unconfirmed bookings (including
appointments earlier today with a known time) say “Attendance unknown”. Summaries
are one or two supporting sentences with a single prepared-demo-review notice;
empty sections use compact inline rows. Counts equal the displayed source rows.
The shared sticky clinician header has a patient identity strip using the
protected profile API. Context-sensitive interface wording changes only the
clinician view, never original documents or patient-facing wording.

For the corrected dashboard browser regression, run
`frontend/tools/check-dashboard-corrections.mjs` with `CARE_DEMO_PASSWORD`,
`PLAYWRIGHT_MODULE` and optionally `HEALTH_ORIGIN`/`CHROME_PATH` supplied locally.
It verifies every source link and displayed count, the comparison window,
attribution, unknown attendance, separate upcoming appointments, the patient
identity strip, mobile layout, sign-out and the clinical-notes lock.

## Clinician comparison periods (command 5)

`agent/server/comparison.py` is the shared, read-only resolver for the dashboard
and clinician appointment summaries. It matches attended/completed consultations
to the authenticated account. A recorded completion/consultation timestamp wins;
otherwise the recorded appointment date/time is used, or the date remains
explicitly date-only. Same-day entries without sufficient clock precision say
“ordering unknown”; the consultation's own source is not listed as its own change.
All clinical comparison and display clocks use `Australia/Sydney`, including DST.

Custom dates start at Sydney midnight, inclusive, and are validated server-side.
“Use last consultation” restores the account's default. The in-memory comparison
preference is shared across clinician dashboard/summary navigation and cleared
when the account changes. It never writes consultation history. First reviews
require a chosen date before any date-based changes summary can be prepared.

Rows preserve event/observation dates, document dates, explicit authoring dates,
clinical receipt dates and actual first upload/import timestamps separately.
Unavailable dates remain unknown. Older dated documents uploaded/received in the
window appear under “Newly received” and never become new medication or symptom
changes. Upload/import time is not relabelled as clinical receipt. Original typed
notes without an observation date use their recording timestamp and the explicit
label “Observation date not supplied”. Unresolved documented requests stay visible
outside the selected period, with their original date; appointments stay separate.

New clinician appointment summaries reuse the exact dashboard period and source
selection, the existing current-medication/allergy/condition sections, source
links, renderers, page budget and export workflow. The chosen comparison/source
context is pinned in the summary event so restore/print does not move its basis.
Clinician summaries are account-scoped and do not become the patient workflow's
“previous summary”. The patient/carer workflow retains its chosen-date/previous-
patient-summary/90-day basis and labels it explicitly. Older saved summaries are
not rewritten. Export budgeting may omit rows and reports the omission count.

`tests/test_comparison_period.py` uses isolated sources/clocks for late receipts,
missing dates, same-day ordering, Sydney midnight/DST, per-account defaults,
custom/reset, first review and summary restoration. No edge-case dates are written
to Jane's existing demonstration sources.

The clinician review opens with at most five grouped issues across Reported changes,
New documents & treatment updates, and Outstanding follow-up. “View all changes”
counts issues; source counts are separate. The walking-support demonstration groups
Emily’s and Morgan’s contributions without treating them as independent corroboration.
Issue details preserve original text, source links and separate date clocks. The
per-account in-memory comparison context also retains expanded-list and return-scroll
state during navigation. No review status is created. The explicitly identified
accidental demo note `01M62NQPN2MQ6VB8DJ9ZJHVTEP` is omitted only from the active
review projection; its original event and protected source endpoint remain available.

`frontend/tools/check-compact-review.mjs` verifies the five-issue opening view,
all issue/detail/source links, expanded return state, custom dates and responsive
layout against a running demo. Supply `CARE_DEMO_PASSWORD` and optionally
`PLAYWRIGHT_MODULE`, `CHROME_PATH`, and `HEALTH_ORIGIN` through the environment.

The revised review-v2 dashboard adds three overlapping views (Needs review,
Action required, Reviewed) over the same grouped issues. The desktop uses a compact list beside a persistent details/action panel, with
a full-screen detail view on mobile. The comparison context retains
tab, expanded-list state and scroll position when the drawer closes. Workflow
status is loaded from the backend event-log projection, not browser flags. Generic
clinical follow-up actions and appointment-request actions can be explicitly
resolved with a recorded basis; reviewing/releasing does not resolve them.

Patient Shared documents and the homepage show approved in-app messages and released
originals through `SharedCare`. The demo view explicitly states its shared recipient
simulation. Date, reporter, review, communication and release details remain behind
the clinician issue click; the opening view is limited to five issue rows.


The reference-layout update keeps the existing system font, local SVG navigation
icons, lavender surfaces and purple controls. Laboratory/test names come from
source result metadata (for example Sodium and Haemoglobin in the current record);
no result is invented merely to match a mockup. Ordered pathology panel names
come from the source report metadata.
A red Abnormal label follows explicit source laboratory flags. The clinical-priority
selector shows a clinician's saved assessment, or Not yet assessed; the original
laboratory critical flag remains separate. Notify patient/carer opens the existing
appointment-request recipient/message preview. It does not mark the patient notified,
acknowledged or discussed, and does not release any source. Prior communications,
internal actions and audit history remain available in the detail panel.

`check-review-workflow.mjs` delegates to `check-reference-review.mjs`, which exercises
the current two-column and mobile layout against the disposable fixture on 7788.
The selected issue, tab, period, expansion and return scroll are memory-only view
state scoped to the signed-in account; persisted clinical workflow stays in the log.


Conventional pathology reports are authored by `agent.demo.pathology_reports`.
The active fictional sources use ordered panel names and complete result tables
rather than abnormality descriptions as report titles. Additional panel analytes
are explicitly newly authored demo content; previously recorded key results are
retained. The append-only upgrade preserves older PDF bytes, dates and audit events.
Reviews and releases of an earlier edition do not approve an expanded replacement.
The dashboard opens the protected original PDF directly for these reports; the
readable source view and retained earlier editions remain available in evidence.

Patient/carer notifications live at `/notifications`. The homepage links to that
screen rather than mixing message forms into the document library. Appointment
requests open a fictional Dr Smith availability list, then a booking preview and
explicit confirmation. One append-only event stores both the linked booking and
calendar entry; retries do not duplicate bookings and unavailable slots are refused.
Booking does not acknowledge a message, mark a result discussed/reviewed, release
a document or advance either clinician's consultation comparison date. Requests
closed by a clinician cannot receive a new booking. Availability is local demo data,
not a real practice scheduling integration; recipient separation remains simulated.

Both workspaces use one document list with ten entries initially and View all for
the remaining originals. The catalogue includes existing pathology panels, the
cardiologist letter and other medical sources; uploaded files join the same list.
There are no Example documents/My uploads collections. In the patient view,
unreleased clinician reports expose only permitted catalogue metadata and stay
locked until the existing review/release workflow permits original-file access.

My health record and clinical confirmation are removed from patient navigation.
For the refreshed Jane demo, clinical confirmation pages/APIs require a clinician
session (and CSRF for writes). Record an update includes shared patient/carer note
corrections. A correction appends a linked entry, retains original wording, uses
an optimistic revision check and cannot change a clinical report or staff note.
Unknown/shared authorship is labelled rather than claimed as authenticated Jane.

Run `check-patient-portal.mjs` against the disposable 7788 fixture for notifications,
booking/calendar, document permissions, note corrections and responsive verification.
