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

The dashboard is a read-only projection of Jane's existing authored demo
sources and explicit bookings. The default comparison is the authenticated
clinician's latest completed/attended consultation (James: 21 October 2026;
Olivia: 26 March 2025). A clinician without one must select a comparison date.
No generated-summary timestamp becomes a consultation date. Prepared source
excerpts are labelled as awaiting clinician review; there is no live generation,
new review-completion state, task-management feature or other-patient inbox.
Source-event dates determine the comparison interval; outstanding follow-up
lists recorded bookings whose completion is not recorded, not inferred tasks.

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
