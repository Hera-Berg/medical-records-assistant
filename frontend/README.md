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
