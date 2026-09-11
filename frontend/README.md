# The interface

Vite + React + TypeScript + Tailwind, built to static files and served by
FastAPI at `/`. One origin, no dev proxy in production, no CDN, no webfont.

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

## Constraints

These are not style preferences. Each has a reason, and they are enforced by
review rather than by a linter.

- **Three type sizes and two weights.** Body, section heading, page title;
  regular and semibold. Density comes from tight leading and small margins,
  never from small text: this is read at arm's length in bad clinic lighting,
  and hierarchy bought by shrinking things disappears first. The page title is
  the only size that has ever been added, and it was added *upwards* — nothing
  on the page is smaller than it was.
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
