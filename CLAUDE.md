# CLAUDE.md — Patient-Held Health Record Agent

Read this before writing any code. It defines invariants that override convenience.

@MODELS.md

## What this is

A self-hosted, single-user agent that maintains a longitudinal health record owned entirely by the
patient. It ingests photographed scripts, specialist letters, pathology PDFs, meal photos, wearable
exports and voice notes, and derives a structured wiki: current medications, allergies, a dated
symptom timeline, and a picture of daily life. Every claim in the wiki cites the raw artefact it came
from, and that artefact is kept next to it.

Runs at `http://127.0.0.1:7777`. All data lives in one folder the user nominates, which is expected
to be inside Dropbox / Google Drive / Nextcloud. We never talk to those services' APIs — we read and
write plain files and let the sync client do its job.

## Non-negotiable invariants

1. **The event log is the only source of truth.** Everything in `wiki/` and `.agent/` is derived.
   Deleting both and rebuilding from `raw/` + `events/` must produce a byte-identical result. There
   is a test for this and it must never be skipped.
2. **The model never mutates state.** Model output becomes a *proposed claim* event. Views are
   regenerated from events. There is no code path where an LLM response writes to `wiki/`.
3. **Data stays on infrastructure the user controls.** No telemetry, no analytics, no crash
   reporting, no CDN fonts or scripts. Vendor every frontend dependency. Inference may run on another
   machine the user owns, reached over their own tailnet — but the endpoint is allowlisted to private
   address space, and reaching a commercial API is a startup failure, not a config option. See
   "endpoint safety" in `MODELS.md`.
4. **Absence of evidence is never evidence of absence.** Nothing is removed from the active
   medication list because it stopped being mentioned. See "staleness" below.
5. **High-consequence claims never auto-apply.** See the consequence tiers table.
6. **The folder outlives the app.** Assume the app is dead in five years and someone opens the folder
   in Finder. Markdown, JSON, JSONL, and original files only. No proprietary formats, no database as
   primary storage, no filenames that need the app to decode.
7. **No clinical advice.** The system reports and cites. It does not diagnose, triage, suggest
   causes, or flag "concerning" trends. This is a product decision, not a disclaimer — do not build
   the feature and then caveat it.

## Stack

- **Backend**: Python 3.11+, FastAPI, uvicorn, bound to `127.0.0.1` only.
- **Frontend**: Vite + React + TypeScript + Tailwind, built to static files and served by FastAPI at
  `/`. Single origin, no dev proxy in production.
- **Models**: see `MODELS.md`, which is normative. Summary: the vision-language model runs either on
  this computer — a `llama-server` the app downloads, verifies and manages, the default — or on another
  computer the user owns, over an OpenAI-compatible endpoint on their tailnet. The choice is per
  machine. `faster-whisper` always runs locally so voice capture works with no network. Both behind
  `agent/llm/` and `agent/asr/`, with the managed process in `agent/runtime/` — never import a
  model-specific SDK outside those modules.
- **Document text**: `pdfplumber` for text-layer PDFs and `pytesseract` as a second opinion on
  images. This is *not* the primary reader — the VLM is. Deterministic text extraction runs
  alongside it as a cross-check. See "dual-path extraction" in `MODELS.md`.
- **Index**: SQLite at `.agent/index.sqlite`, treated as a disposable cache. Never the only home of
  any fact.
- **Config**: single `config.toml` at the vault root. Vault path, port, model endpoint, model names.
  **No secrets in it, ever.** The vault syncs to Dropbox/Drive/Nextcloud, so anything in that file has
  been handed to a third party by definition. Credentials live outside the vault — see `MODELS.md`.

Do not add: an ORM, a task queue broker, Docker as the primary install path, or any auth framework.
Background work uses a simple in-process worker with a JSONL job file so it survives restart.

## Settled decisions

Made during phase 1. Binding on every later phase. Do not relitigate; if one genuinely blocks you,
raise it rather than working around it.

**Device identity lives outside the vault** at `~/.config/health-agent/device`, with a
`HEALTH_DEVICE` override. It cannot come from `config.toml`, because that file syncs to every machine
and they would all write to the same shard — precisely the conflicted-copy data loss the
one-file-per-device rule exists to prevent. Stored as `{id, label, created, hostname, platform}`;
`id` is `{hostname-slug}-{4 random chars}`. A hostname/platform mismatch at startup means the file
was cloned or restored from backup: refuse to append, require re-issue or `HEALTH_DEVICE`.

**`sync_profile` is not a storage abstraction.** `local | dropbox | gdrive | nextcloud | other`,
default `local`. Every option is a folder on disk; the desktop clients present as ordinary
filesystems and we never call their APIs. There are no `LocalBackend` / `DropboxBackend` classes and
there never will be — a backend interface is the seam through which someone later adds a real API
client and silently breaks the privacy model. The profile selects only: which conflict filename
patterns the scan reports, placeholder handling, readback verification on virtual drives, and setup
warning copy. Per-device sharding applies under `local` too, so the invariant holds when the folder
is later moved into a sync client.

**Shard filename grammar, not the profile, keeps forks out of the merged view.** Anything failing
`YYYY-MM.{device}.jsonl` is excluded regardless of profile, so a misconfigured profile cannot cause
double-counted events. The profile only labels the fork in the report.

**Ordering is `(ts, id)` parsed, never string-compared.** `…11.5Z` sorts before `…11Z` as text.

**Torn final lines are never rewritten.** A crash mid-write leaves truncated bytes as their own
malformed-and-reported line; the next append leads with a newline and lands cleanly. Capture never
fails because a previous process died.

**Placeholders are probed, not inferred from `st_blocks`.** Filesystems that inline small files
report zero blocks for real data. Appending to a shard that has not downloaded is refused outright.

**Secrets are rejected at config load.** Any key matching `key|token|secret|password` with a
non-empty string value is a startup failure. `api_key_env`, `header` and `scheme` are allowed as
references.

**`check` writes nothing** unless `--fix` is passed.

Added in phase 2:

**Sidecars append `.json` to the full artefact name**, not replacing the extension —
`…_a3f91c.jpg.json`. Replacing it collides when the artefact is itself JSON, and leaves a directory
scan unable to tell sidecars from artefacts since both parse as valid names.

**`ingested_ts` drives the raw filename**, never `captured_ts`. See the four-timestamp table.

**Raw artefacts are `0o400`, sidecars `0o600`.** A guardrail against accident, not a security
control — directory permissions still allow deletion, so never describe it as immutability. A mode
change is not corruption: restore-from-backup and resync both lose modes and `verify()` must not
report that as a data problem.

**Short hashes lengthen on collision**, never overwrite. 24 bits collides around four thousand
artefacts and a lifetime record will pass that. Prefix length is a module constant so tests can
force real collisions.

**Recorded paths are validated before any write.** A path in an event payload that is absolute or
climbs out of the vault is refused — hand-edited and sync-corrupted payloads are both realistic.

**OS metadata files are ignored, not reported.** `.DS_Store`, `Thumbs.db`, `.nextcloud` markers.

Added in phase 3:

**Projection is a pure function of `(events, as_of)`.** No wall-clock read inside it, no generation
timestamp in any derived file. `as_of` defaults to now only at the CLI boundary.

**Determinism has three environmental leaks, all closed by construction**: the clock (`as_of`),
newlines (binary writes with explicit `\n`, never text mode), and locale (fixed month-name table,
never `%B`/`%A`, code-point sorting only). The rebuild test replays under a different TZ and LANG.

**Consequence tier is recomputed from the predicate, never read from the payload.** The payload's
value is kept for provenance and a disagreement is reported as an anomaly, but code decides. This is
what makes the gate hold against a buggy or compromised extractor rather than a cooperative one.

**The writer deletes only files the previous manifest lists, whose bytes still match.** Anything
else in `wiki/` is foreign — a hand-dropped note, a sync client's conflicted copy — and is reported,
never removed. A missing manifest authorises zero deletions.

**Render the literal, compare the normalised.** Every value carries the source's span and a
normalised key. `5mg` renders `5mg`, never `5.0mg`. Arithmetic uses `Fraction`/`Decimal`; a float
never reaches frontmatter.

**Number words parse conservatively.** "a tablet" is not quantity 1 unless followed by a recognised
unit, and a span with no cardinal yields nothing rather than a guess. A wrong quantity ages a
medication to `stale` on fiction.

**Superseded readings are rendered, never discarded.** A value replaced by a correction, a later
script, or a higher-tier source appears in the entity's `## Earlier readings` section with its
citation and what replaced it. "What did I correct, and from what" must be answerable from the
folder alone — a correction whose original has vanished is unverifiable, and a mistyped correction
becomes undetectable.

**Rejected content never renders.** A correction is an assertion — the superseded value stays
visible so the assertion can be checked. A rejection is a retraction, and printing the content
re-asserts what the user said is not true of them. This matters because the wiki gets handed to a
clinician: someone who rejects a mis-OCR'd "alcohol dependence" must not find it on their problems
page under any heading. No `## Rejected` section, not in entity pages, not in exports.

**But a rejection must be durable in reconciliation.** Re-extraction must not resurrect a rejected
claim — the same `(subject, predicate, normalised value, artefact)` proposed again stays suppressed
until the user says otherwise. This is the same failure class as a correction being overridden by a
later model read, and needs its own test. An artefact whose claims were all rejected is *reviewed*,
not unprocessed, and must not re-enter the review queue.

The owner can still audit their own rejections — phase 7 shows review history — but that is a view
of the log, not content in the record.

**Two contradictory user decisions on the same reading: the later one governs, and the ambiguity is
raised.** If a reading was confirmed and later rejected, the content is withdrawn — a rejection is an
explicit user act and it is the more recent one, and everywhere else in the system the latest
decision wins among acts of equal authority. Keeping the earlier confirmation standing would leave
rejected content in a document that gets printed. Because the direction of the mistake is unknowable,
raise a review item naming the artefact and asking which was meant, **without reproducing the
content**. Withdrawing then re-confirming is one tap; un-printing a clinician's copy is not.

**Date coercions widen, never sharpen, and are always reported.** An unrecognised `precision` widens
to a year. An unusable `uncertainty_days` widens one precision step — `day → month`, `month → year`,
`year → year` — because the field's presence asserts uncertainty beyond the precision unit while its
value says nothing about how much. Stepping through the schema's own granularities invents nothing;
falling back to the bare precision band would be sharper than the payload claimed, and dropping the
date to null discards information the record exists to keep. Every coercion raises an anomaly against
the claim's subject, so a reader seeing a vague date finds the reason on the same page.

**Salt variants alias in the projection, not at extraction and not in `subjects.py`.** Pharmacy
labels carry salt names — `levothyroxine sodium`, `metformin hydrochloride`, `perindopril arginine` —
and creating a separate entity for each duplicates the medication list *and* silently breaks
staleness, aging one copy while the other stays active. Aliasing belongs in the projection's alias
layer beside confirmed merges, so an improved table is picked up by a rebuild with no re-extraction
and no model call. `subjects.py` is path safety; a drug vocabulary does not belong in it.

**The table is explicit `(full name → base name)` pairs, never a suffix-stripping regex.** Salt
choice can change the number: perindopril arginine 5mg is the equivalent of perindopril erbumine 4mg.
Blanket stripping turns that into a false dose conflict, or ranks one silently over the other. The
salt is recorded on the claim, so two salts giving different numbers surface as `conflicted` — a
false alarm cleared in one tap beats showing 4mg to someone taking 5mg. Aliasing is disclosed on the
entity page the way date coercions are: the label read X, filed under Y.

**An aliased entity gets no stub page.** A confirmed merge keeps one because it records a user
decision that must stay visible and reversible. A table alias is normalisation, not a decision — a
rebuild from scratch would never have created the page — and the entity note plus the artefact
citation already disclose the label's own wording.

**`claim.proposed` carries the source's own subject wording** alongside the normalised subject id.
Everywhere else the codebase renders the literal and compares the normalised; subjects were the one
place that rule wasn't applied, so normalising the slug would have destroyed the only copy of what
the label actually said. Additive payload field; older events fall back to current behaviour.

**One review item per fact, not per source.** Two documents asserting the same normalised value for
the same slot are one review, confirmed in one tap, emitting one `claim.confirmed` per claim — the
wiki already cites multiple sources for one fact, and a queue that asks twice about one fact is what
makes inboxes uncompletable. Same slot with *different* values stays separate: that is a conflict,
not a duplicate. Extraction is unchanged — idempotency is keyed per artefact, so a second document
stating the same fact must still produce a second proposal.

**Rejection suppression is keyed on `(subject, predicate, normalised value, artefact)`**, built from
the whole log before any claim is admitted — which is what makes it hold when a re-extraction sorts
earlier than the rejection. Normalised, so `5.0mg daily` can't slip past a rejection of `5mg daily`.
The artefact stays in the key: re-reading the same photograph is the case the user decided; a
different document saying the same thing is new evidence and theirs to decide again.

**Reconciliation tracks per-artefact review state** — claims, decided, rejected, `is_reviewed`,
`all_rejected`. An artefact whose claims were all rejected is reviewed, not unprocessed, and must
never re-enter the review queue.

**Anomalies live in the rebuild report, not the wiki**, when they have no subject — a malformed
payload or a shard naming violation is integrity information about the log, not record content.
An anomaly that *does* resolve to a subject (a payload disagreeing with the computed consequence
tier for `med:perindopril`) attaches to that entity as well. Anomalies are regenerable from the log,
so they need no persistence, but they must be surfaced: `/api/health` in phase 5 and the review
inbox in phase 7 both report the count, so they cannot scroll past unseen.

Added in phase 10:

**Retrieval reads the projection, never the event stream.** Reconciliation is
what applies the rejection suppression, so building only from reconciled slots,
assembled entities and timeline rows is what makes "rejected content appears in
no answer" hold by construction rather than by this layer remembering to check.
The same rule `agent/server/serialise.py` follows, for the same reason.

**And a second withholding over free text, keyed on `(wording, artefact)`.** A
rejection retracts a claim, not a recording, so a transcript can still carry the
words — legitimately, on the timeline, where the record shows everything it
holds. An answer is produced on demand by a question aimed at it and then read
aloud, so a passage carrying a rejected reading's own wording is withheld,
silently, because naming it would point straight at it. Keyed on the artefact as
well as the wording, exactly as the suppression key is: a *different* document
saying the same thing is new evidence and the user's to decide again.

**Interpretation is refused at the question, by a table of phrasings.** The hard
part of that table is not what it catches but what it must let through: "is my
thyroid result serious" is refused and "what am I taking for my blood pressure"
is not, and both live in the same file with the counterexamples beside them.

**The one expansion round is one because it is a function call.** The model is
shown the question and the titles of wiki pages — nothing read out of an artefact
— and returns words, folded to plain text before use, so a "term" written to look
like a path arrives as the words in it. The vault syncs from a third party and
nothing in it may steer what gets read next.

**A conversation may carry three questions, and a follow-up inherits the
previous turn's subject but not its predicate.** "When was that" is asking about
a date; carrying "dose" into it would answer the first question twice, which is
what a model refining across rounds looks like from the inside. Prior turns are
dropped first when the prompt is tight: the current question's evidence outranks
the conversation's memory. What the architecture forbids is the model refining
across rounds, not the conversation having a memory.

**There is no local record of the questions, and no setting that creates one.**
It would be the one file accumulating what the patient asked, inside a folder
that syncs to a third party, and questions disclose more than the record does. A
setting someone can turn on is a file that can exist.

**A failed question updates what the box's state is; a successful one does not.**
Answering a text question proves the box is reachable and the key is good, and
proves nothing about whether it can read a photograph. "Working" here means the
startup probe passed, vision included.

**A citation must cover the join, not only the facts either side of it.** The
dangerous sentence is not the uncited one — that is dropped and gone. It is "you
take perindopril 5mg daily for your blood pressure", citing a real photograph of
a real script, where the drug and the dose came off the page and the word *for*
came from what the model knows about perindopril. It looks sourced, it reads as
sourced, and the one thing it asserts is the one thing the record never said;
that is more dangerous than an uncited sentence, not less. So when a question
asks what a medicine is for and nothing retrieved says, any sentence using the
question's own words for it is dropped unless the extract it cites contains
them, and a code-written sentence says the record does not record it. The rule
needs no second version for the day the record holds an indication: on that day
the extract carries those words and nothing is refused.

**Every value a passage carries is separated from its provenance by a
separator, never wrapped in brackets.** A value can itself contain brackets —
"November 2024 (±15 days)" — so `value (tier, date)` has no unambiguous end, and
an answer read off one came back with the record's own provenance pulled into
the sentence.

Added in phase 11:

**Where documents are read is chosen per machine**, at `~/.config/health-agent/reader` beside the
device identity, never in `config.toml`. "This computer" written into a synced file is false on every
other machine that reads it. The remote endpoint's details stay in `config.toml`. A vault that already
has `[models.vlm]` starts on another computer; a new vault starts on this one. The default is written
the first time the server starts, so it cannot flip later because a synced config gained a table — and
never by a demo vault, which has no table and would write "this computer" over a real vault's default.

**Binaries and weights are downloaded, pinned and verified — never committed, never compiled.** One
manifest in code carries URL, exact size and sha256 for the `llama-server` build and every model file,
per platform. Git history is forever, and this project's durability claim is about the vault, not the
repo. Files live outside the vault; a data directory inside it is refused.

**The download is the one outbound connection that is not to the user's own machines**, so it never
starts without a confirmation that names the exact bytes, the hosts, where files go and that no part of
the record is sent — enforced by the server, which refuses a start that does not carry the exact byte
count back. Allowlisted hosts, https, checked per redirect hop, resumable, and verified before a byte
is used.

**Guard the act, not a context that correlates with it.** Demo vaults were first blocked from reading
on this computer. That tracked nothing: the files are the machine's, a demo could already reach a
remote box, and a demo plus a local model is the only way to watch extraction work without personal
documents. The harm was an unexpected download, and asking guards it on every vault. A demo vault keeps
its demonstration banner, because what it extracts is still invented.

**A disabled control says why at the control** — in its label or directly beside it, never only as a
general line elsewhere on the page. A greyed control with the reason a paragraph away reads as a bug.

**Record the facts, derive the judgement.** The `degraded-tier` label is retired, not renamed.
`extraction.completed` carries a `runtime` block — `kind`, `engine`, file hashes, elapsed time — and
views derive whatever they say about quality from those. A quality assessment written into an
append-only log cannot be revised when the set of readers changes. `kind` is `bundled | endpoint`,
never "this computer": the event's own `device` already says which machine.

**Identity before the key, on loopback too.** Any web page can send a request to `127.0.0.1` and
llama-server answers cross-origin, so each launch gets a fresh key through the child's environment,
never argv, never disk. It is handed to the client as an explicit credential — never through the
resolver chain, which would fall through to the keychain and send the remote box's key to whatever is
on the port — and only after the process on the port is shown to be this app's child.

**The reader sleeps when idle**: 15 minutes by default, configurable per machine, never while a job is
queued or a request is in flight, and the state says "sleeping" rather than hiding a slower first read.

**macOS and Windows are pinned and unverified** until someone runs them. Say so wherever it matters.

**A reader that misses the bar ships only as an informed choice.** The 4B model is the recommended
local reader despite failing the eval bar, because it is the one that runs on an 8 GB laptop. The
model list shows every model's size, memory, speed and correct/abstained/wrong in the list itself, and
the known failure nothing in the app can catch is said beside it in plain words. Memory shortfalls are
warned about, never enforced.

**A reader fails safely or it is disclosed.** The eval scores correct, abstained and wrong; the bar is
wrong 0% and correct + abstained 100% on medications, doses and allergies. Wrong includes silence and
any unlisted assertion. An abstention counts as safe only because it is visible: every latest reading
that was unreadable, cut off, refused, or that declined part of a page raises a high-consequence
`could-not-read` review item naming what was not read and never a value, until a person marks it dealt
with (`reading.acknowledged`) or a newer reading replaces it. A page whose any turn was cut off
proposes nothing from any turn.

**Identifiers never absorb values, and a dose is never half a dose.** Name, strength and frequency are
separate fields; a name with a digit or unit is refused; strength without frequency is an abstention.
The dose comparison key keeps words it does not understand as `instruction` and counts other than one
as `per_dose`, so two readings never compare equal by dropping what differs.

## Storage layout

The vault root is user-nominated. Everything below is relative to it.

```
health/
  config.toml
  raw/
    2026/09/2026-09-08T1432Z_a3f91c.jpg            # original, never modified
    2026/09/2026-09-08T1432Z_a3f91c.jpg.json       # sidecar: hash, mime, capture context
  events/
    2026-09.elwood-laptop.jsonl                # one file per device per month
    2026-09.elwood-phone.jsonl
  wiki/
    medications/perindopril.md
    allergies/penicillin.md
    problems/hypertension.md
    timeline/2026-09.md
    people/dr-nguyen.md
  exports/
    2026-09-08_cardiology.pdf
  bulk/
    fitbit/2026-09.parquet                     # wearable data, NOT in the event log
  .agent/
    index.sqlite
    jobs.jsonl
    logs/
```

**Filenames**: `{ISO8601 basic UTC}_{first 6 of sha256}.{ext}`. The hash gives free deduplication —
people photograph the same script twice and download the same pathology PDF three times. On
collision, do not re-ingest; append an `artifact.reseen` event instead.

**One JSONL per device per month.** Two devices appending to a shared file on Dropbox produces
`conflicted copy` files and silent data loss. Separate files unioned and sorted at read time never
conflict. On startup, scan for `*conflicted copy*` and `*(1).jsonl` patterns and surface them for
merge rather than ignoring them.

**Wearable data does not go in the event log.** Minute-level heart rate will drown every other event.
Bulk-store it under `bulk/`, and let only derived summaries become events ("resting HR up 6bpm over
three weeks, 2026-08-15 to 2026-09-05").

## Event schema

Append-only JSONL. One JSON object per line, no trailing commas, UTF-8, `\n` terminated. Never
rewrite a line. Never delete a line. Corrections are new events.

```json
{
  "id": "01J8F2K3M4N5P6Q7R8S9T0V1W2",
  "type": "claim.proposed",
  "ts": "2026-09-08T14:32:11Z",
  "device": "elwood-laptop",
  "actor": "agent",
  "provenance": {
    "model": "qwen3.5:9b",
    "model_rev": "sha256:...",
    "prompt_hash": "sha256:...",
    "artifact": "a3f91c"
  },
  "payload": { }
}
```

`id` is a ULID — lexically sortable, collision-safe across devices without coordination.

### Event types

| Type | Actor | Meaning |
|---|---|---|
| `artifact.ingested` | user | A new raw file landed. Payload has hash, mime, size, capture context. |
| `artifact.reseen` | user | Duplicate of an existing hash. |
| `extraction.completed` | agent | Full raw model output for an artefact. Stored verbatim. |
| `claim.proposed` | agent | A structured claim derived from an extraction. |
| `claim.confirmed` | user | User accepted a proposed claim. |
| `claim.rejected` | user | User rejected it. |
| `claim.corrected` | user | User changed a value. **Highest authority.** |
| `entity.merge.proposed` | agent | "Panadol" and "paracetamol" may be the same thing. |
| `entity.merge.confirmed` / `.reverted` | user | |
| `note.recorded` | user | Voice or text note, with transcript. |
| `reading.acknowledged` | user | A "could not be read" item was dealt with. Names the extraction it answers. |

**Store `extraction.completed` separately from `claim.proposed`.** When the model is swapped for a
better one you need to re-derive everything and diff it, and you cannot do that from parsed claims.

### Claim payload

```json
{
  "subject": "med:perindopril",
  "predicate": "dose",
  "value": { "amount": 5, "unit": "mg", "frequency": "daily" },
  "evidence_tier": "prescriber-issued",
  "consequence": "high",
  "confidence": 0.82,
  "occurred_at": { "value": "2026-06-04", "precision": "day", "uncertainty_days": 0 },
  "artifact_ts": "2026-06-04T00:00:00Z",
  "captured_ts": "2026-09-02T09:11:00Z",
  "ingested_ts": "2026-09-02T09:14:03Z"
}
```

**Four timestamps, always.** These diverge constantly and conflating any two corrupts the timeline.

| Field | Meaning | Known? |
|---|---|---|
| `ingested_ts` | When the bytes entered the vault. Drives the raw filename. | Always |
| `captured_ts` | When the photo/recording was made. | Only on live capture, or later from EXIF |
| `artifact_ts` | When the artefact itself was created — script written 4 June. | Needs a model read |
| `occurred_at` | When the thing happened — symptom onset. | Needs a model read |

Only `ingested_ts` is always known. **Every other field is explicit `null` until genuinely
established.** Never substitute one timestamp for another to fill a gap — ingest time is not capture
time the moment someone drags in a photo taken three days ago, and that substitution is invisible
until it has already corrupted months of timeline.

**Date uncertainty is first-class.** A note says "sometime in June" — store
`{"value": "2026-06-15", "precision": "month", "uncertainty_days": 15}` and render the band. A
timeline that fakes precision is worse than one that shows fuzz. Never let the model pick an exact
date to satisfy a schema — the extraction prompt must offer the uncertainty fields explicitly.

**Unresolvable temporal references are preserved, never dropped and never auto-resolved.** "Around
Easter", "last Christmas", "the week before the wedding" — the model copies the phrase verbatim and
emits `occurred_at: null`. It never computes a date. The projection then raises a *dateable* review
item carrying a candidate computed in code where one exists — the computus is deterministic, the
year is not — for the user to confirm in one tap. Discarding the phrase loses information the record
exists to keep; resolving it silently invents the year.

### Evidence tiers

Rendered distinctly everywhere, always. A clinician must be able to tell these apart in a second.

| Tier | Source |
|---|---|
| `prescriber-issued` | Script, discharge summary, specialist letter |
| `lab-issued` | Pathology or imaging report |
| `device-recorded` | Wearable, BP cuff, glucometer |
| `patient-reported` | Voice note, typed entry, meal photo |
| `inferred` | Derived by the model from other claims. Always needs confirmation. |

## Reconciliation rules

This is the hard part of the project. Get it right before building anything pretty.

1. **Corrections outrank extractions regardless of order.** If a user fixes a mis-OCR'd dose and a
   later re-extraction with a better model contradicts it, the correction still wins and the
   contradiction is raised as a conflict for review. Naive replay-by-timestamp reintroduces
   corrected errors — this is the single most likely bug in the system, so test it directly.
2. **Higher evidence tier wins ties**, with a more recent `occurred_at` breaking ties within a tier.
3. **Contradiction without resolution is a visible state.** If two prescriber-issued sources give
   different doses, the wiki shows both with their sources and marks the entity `conflicted`. Do not
   silently pick one. Do not average.
   **The same applies to any user act the projection declines to honour.** A confirmation, correction
   or rejection that the rules do not act on must still appear in the wiki and the review queue,
   with its source. Gating an action is legitimate; making it disappear is not, and is worse than the
   outcome the gate was protecting against — the user believes they told the record something.
4. **Staleness, not deletion.** Compute an expected exhaustion date from the script (30 tablets, one
   daily, no repeats → 30 days). Past that with no confirming evidence, mark the medication
   `stale — last confirmed 8 months ago`. It stays on the list.
   Moving to `stopped` requires an explicit user action: either a `claim.corrected` carrying
   `status: stopped`, or a `claim.proposed` stop the user explicitly confirmed. The rule's target is
   **inference from silence**, not the model reading a document that says to cease. Two constraints
   on the confirmed-proposal path: the proposal must be `prescriber-issued` or `lab-issued` —
   `device-recorded`, `patient-reported` and `inferred` cannot transition status — and the review UI
   must render stop proposals as their own distinct action, never as a generic accept in a
   tap-through queue. A careless tap must not be able to drop a medication.
   **A stop below that tier annotates rather than transitions, and is never discarded.** Status stays
   `active` or `stale`, and the entity gains `stop_reported` and `stop_reported_tier` in frontmatter,
   a cited sentence in the body, and an entry in the review queue. A patient reporting they stopped
   taking something is real information — they are the authority on what they actually take, while
   the prescriber is the authority on what was prescribed — and a record showing both, with the
   discrepancy visible, is more useful to a clinician than either alone. This is a discrepancy, not a
   `conflicted` status; that state is reserved for contradictory sources for the same fact.
   A `stopped` medication keeps its file and its full history. Nothing is ever removed.
5. **Merges are reversible events, never silent normalisation.** Map to ATC/RxNorm codes where
   possible as a *hint* to the merge proposer, but treat the mapping as fallible and always ask.

## Consequence tiers — what gates review

Gate on consequence, not model confidence. A confident wrong allergy is the failure that matters.

| Tier | Examples | Behaviour |
|---|---|---|
| **high** | Add/remove allergy, add/stop medication, dose change, add/remove diagnosis | Never enters the wiki without an explicit user tap. No exceptions, no confidence threshold that bypasses this. |
| **medium** | Symptom onset, new practitioner, out-of-range lab value, procedure | Batched into a weekly review. Auto-applies after 7 days if untouched, and is marked `unreviewed` in the wiki until confirmed. |
| **low** | Meal photo, activity summary, weight, general note | Auto-applies. Reversible from the timeline. |

Inbox debt is what kills these systems. Two hundred unreviewed items and the record is worthless. The
review queue must never accumulate low-tier items, and the weekly review must be completable in two
minutes.

## Wiki format

Markdown, YAML frontmatter, one file per entity. Frontmatter is the machine-readable canonical state,
the body is human prose. Regenerated wholesale from events on every rebuild — never hand-patched.

```markdown
---
id: med:perindopril
name: Perindopril
status: active            # active | stale | stopped | conflicted
dose: 5mg daily
started: 2024-11-02
last_confirmed: 2026-06-04
expected_exhaustion: 2026-07-04
evidence_tier: prescriber-issued
sources: [a3f91c, 77b210]
---

Started by Dr Nguyen for hypertension.[^a3f91c] Dose unchanged since commencement.

Last confirmed by a script dated 4 June 2026; 30 tablets, no repeats. Expected to have run out
around 4 July 2026 — confirm whether this is still being taken.

[^a3f91c]: Photographed prescription, 2 September 2026 → `raw/2026/09/2026-09-02T0911Z_a3f91c.jpg`
```

Per-entity files, not one big list — it gives clean diffs and per-fact citation granularity. "Current
medications" is a **generated view**, not a stored file. Timeline files are one per month.

Footnote citations pointing at relative paths into `raw/` survive any renderer and stay legible in a
plain text editor. Use them everywhere. A sentence in the wiki without a footnote is a bug.

## HTTP API

```
GET  /                          SPA
GET  /api/health                model reachable, vault writable, queue depth
POST /api/capture               multipart; returns immediately, processes async
GET  /api/timeline?from=&to=    merged event view
GET  /api/wiki                  entity index
GET  /api/wiki/{id}             entity + full source chain
GET  /api/artifact/{hash}       original bytes, correct mime, inline disposition
GET  /api/review                pending queue, grouped by consequence tier
POST /api/review/{event_id}     confirm | reject | correct
POST /api/summary               generate consultation summary
POST /api/ask                   one question about the record; writes nothing
GET  /print/{summary_id}        print-optimised A4 HTML
POST /api/rebuild               wipe wiki/ + index, replay events
GET  /api/files?path=           one folder in the vault, or one file's description
GET  /api/files/content?path=   a text file, for reading in place
DELETE /api/files?path=         remove one file or one empty folder
GET  /api/settings              storage profile, inference endpoint, key state
POST /api/settings/sync-profile where the folder already is
POST /api/settings/endpoint/connect  reach the box, check it, save it if it works
POST /api/settings/endpoint/key      store the key in the OS keychain. Write-only.
POST /api/settings/reader       read on this computer, or on another computer; sleep-after minutes
GET  /api/reader                the bundled reader: files, download progress, state, measured speed
POST /api/reader/download       start or continue the one-time download. Never automatic.
POST /api/reader/cancel         stop a download, keeping what has arrived
POST /api/reader/retry          start a reader that stopped after repeated crashes
```

**Connecting is one action.** `connect` asks the box what it runs, runs the
startup probe against it — vision included — and writes `config.toml` only if
every check passed. There is no route that saves an endpoint known not to work:
that produces a screen saying it is configured above a queue that silently never
drains. It writes **four keys** by the same surgical line rewrite `sync_profile`
uses, so comments and every unrelated value survive.

The address guard runs first, before a credential is read and before anything is
sent. The key never goes through `config.toml` and never comes back out of any
route: `/api/settings` reports `configured | not set | unusable` and the *place*
a key was found, which is a place and not a secret.

Every sentence the connect route can put on screen is written in
`agent/server/endpoint_check.py` and selected by a code, so nothing the box
composed reaches the browser — the same rule `/api/health` follows. The two
exceptions are the address guard's refusal and the credential resolver's, both
of which speak before the first byte leaves the machine, when there is no
far-end text in existence to leak.

`/api/files` is the folder browser behind the **Files** screen — the folder is the
record, so the app shows it. Deletion is tiered by what the path *is*, and the tiers
are enforced server-side: `wiki/` and `.agent/` freely (a rebuild writes them again),
`raw/` deliberately and only after being told how many claims were read off the
artefact, `events/` **never at any tier of confirmation**, and `config.toml` never.
A UI that can unlink a shard is a UI that can destroy the record; removing the vault
is the file manager's job. Every path is checked by shape and again after symlink
resolution.

`/api/ask` is the one route that runs inference while somebody waits, and that is
deliberate: the answer *is* the request, and there is nothing to queue it into
because answers are ephemeral. POST rather than GET for a read, because the
question is content and a GET puts it in browser history and in every proxy log.
It appends nothing, and there is no record of what was asked — see phase 10.

`/api/capture` must return before any inference runs. The user is in a waiting room; never block the
UI on a 9B model.

## Frontend

### Recorder

Browser `MediaRecorder`, not dedicated hardware.

- Enumerate inputs with `navigator.mediaDevices.enumerateDevices()`, filtered to `audioinput`.
- **Device labels are empty until permission is granted.** Request `getUserMedia` first, then
  re-enumerate, or the picker shows blank entries. This will look like a bug and isn't.
- Persist the chosen `deviceId` in `localStorage`, reselect on load, fall back to default gracefully
  when that device is gone (unplugged headset).
- Handle `devicechange` — repopulate the list live.
- One large record button. Space bar toggles. Live level meter so the user can see it's working.
  Elapsed time. No other decisions at capture time — do not ask for a category, title, or tag.
- `MediaRecorder` produces `audio/webm;opus` in Chrome and `audio/mp4` in Safari. **Store the
  original as-is** in `raw/`; transcode a 16kHz mono WAV copy into a temp dir for Whisper and delete
  it after. Feature-detect with `MediaRecorder.isTypeSupported`.
- Upload in the background with a visible but non-blocking indicator. Queue to IndexedDB if the
  backend is unreachable and drain on reconnect.

`getUserMedia` requires a secure context. `http://127.0.0.1` and `http://localhost` qualify.
`http://192.168.x.x` **does not** — recording from a phone on the LAN will silently fail. If the host
is not localhost, detect it and show a clear explanation rather than a broken button.

### Capture

Same principle: one action, classify later. Drag-drop anywhere on the window, paste from clipboard,
file picker, camera capture on mobile via `<input capture>`. Never ask what kind of document it is —
that is the model's job.

### Review inbox

Grouped by consequence tier, high first. Each item shows the proposed change as a **diff against
current state**, the artefact thumbnail beside it, and three actions: confirm, correct, reject.
Correct opens an inline field, not a modal. Keyboard-driven: `j`/`k` to move, `y`/`n` to decide.

### Timeline

Vertical, reverse-chronological, dense. Date-uncertainty rendered as a band, not a point. Evidence
tier shown as a small consistent marker on every row. Clicking any row opens the source artefact.
Filter by entity, tier, and date range. This is the screen the user opens most — make it fast and
make it scan.

### Consultation summary

One page, hard limit. A GP will not read three. Sections in this order: **what changed since last
visit**, **current medications**, **active problems**, **the question the patient came with** (typed
by the user, not generated).

- Printable A4 with no login and no app chrome. Many clinicians will not take a phone from a patient;
  a handed sheet of paper gets read.
- During the consult, a fast tap on any line reveals the source artefact full-screen.
- Strictly factual and sourced. Zero interpretation, no suggested diagnoses, no highlighted trends. A
  patient producing an AI summary can read as a challenge, and this is exactly the situation where
  clinician defensiveness does the most damage. Let the evidence tiers do the persuading.

### Design

Read `/mnt/skills/public/frontend-design/SKILL.md` before writing UI code. Beyond that: this is a
medical record, not a dashboard. No gradients, no cards-with-shadows, no data-viz flourish, no
progress rings. Dense, typographic, high-contrast, readable at arm's length in bad clinic lighting.
System font stack — no webfonts, since we make no network calls.

## Build order

Do not start a phase before the previous one's tests pass.

1. **Vault + event log.** Config, folder scaffolding, ULID event append, multi-device union read,
   conflicted-copy detection. Test: 10k events across 3 device files replay in deterministic order.
2. **Ingest + raw store.** Hashing, dedupe, sidecars, mime detection. No model yet.
3. **Projection engine.** Events → wiki markdown. Reconciliation rules, staleness, conflicts. Test:
   correction-then-re-extraction preserves the correction. Test: rebuild is byte-identical.
4. **Extraction.** LLM client, prompt templates with hashing, structured output validation, claim
   proposal. Reject and log malformed model output — never coerce it into a valid claim.
5. **HTTP + SPA shell.** Serve, capture endpoint, timeline read.
6. **Recorder + transcription.** Device picker, MediaRecorder, Whisper. **Entirely local**: a
   recording is stored and typed up with the box asleep, off the tailnet, and with the queue parked
   on a rejected key. The output is a transcript stored verbatim as `extraction.completed`, with
   word-level timestamps so a claim can cite the seconds it came from. No claims are proposed from
   it — see phase 7.
7. **Review inbox.** Consequence gating, correction flow. **Also: claims from transcripts.**
   Proposing `med:atorvastatin dose 40mg daily` from a voice note is the vision-language model
   reading the transcript as text, which needs the box — so it was deliberately kept out of phase 6,
   whose whole point is that a voice note is captured and typed up with no network at all. It is
   listed here rather than left as "later" because a deferral nobody wrote down is a dropped
   requirement: the two voice fixtures in `tests/fixtures/` carry expected claims that nothing scores
   until this is built, and `health-agent eval --speech` scores only their transcripts in the
   meantime.
8. **Consultation summary + print view.**
9. **Wearable bulk import.** Fitbit/Apple Health export parsing, summary-only events.
10. **Querying the record.** Not before phase 9. See below.
11. **Reading on this computer.** A managed `llama-server` and `Qwen3.5-4B`, downloaded and verified
    on first run, as the default reader. See "Where documents are read" in `MODELS.md`. Not the default
    until the bundled reader meets the eval bar in its own right: wrong 0%, correct + abstained 100%.
12. **The `indication` predicate.** What a medication is *for*, read off the
    documents that say it. Its own piece of work — see "Next: the `indication`
    predicate".

## Querying the record

Deferred to phase 10 deliberately — it depends on the wiki, the index and citations all being solid,
and building it early produces a system that sounds impressive and cites nothing. Do not build
abstraction for it in earlier phases. No changes to phases 1–9 are needed to enable it.

Scope: **questions about the record, answered only from the record.** "When did I start
perindopril?" "What did Dr Nguyen say about the statin?" "How many times have I mentioned the
headaches?" Not "what does this result mean" and not "should I be worried".

Architecture: **deterministic retrieval, single grounded generation.** Not an agent loop.

1. Classify the question's *shape* in code and retrieve accordingly. An entity mention resolves by
   exact and normalised string match plus the salt table — but many questions name no entity, and
   retrieval handles those too: by kind ("what allergies do I have"), by person ("what did Dr Nguyen
   recommend"), by predicate ("what doses changed"), by recency ("what happened last month"), by
   kind of document ("what did the blood test say"). A question carries several of these at once and
   they union rather than competing. No model call.
2. Retrieve the matching entity files and the events that produced them, bounded to a fixed budget.
3. If that retrieves nothing or too little: **one** bounded expansion where the model proposes
   *search terms* — never file paths, never which files to open — and code retrieves against them
   deterministically. Exactly one round.
4. One generation pass with that context and the question. No planning, no tool calls, no
   multi-turn refinement. A conversation may carry three questions; prior turns feed the classifier
   and the context, and are dropped first when the prompt is tight.

Rules, all enforced in code rather than requested in the prompt:

- **Every sentence carries a citation** to an artefact or event. A sentence that cannot be attributed
  is dropped before rendering, not shipped with a hedge.
- **Empty retrieval returns "nothing in your record covers that."** It never falls through to the
  model's general knowledge. Test this with a question whose answer the model certainly knows and the
  record certainly does not contain.
- **Refuse interpretation, by classifier not by vibes.** Questions asking what something means,
  whether something is serious, or what to do get a fixed response pointing at the consultation
  summary feature. Draw the line at the question, before retrieval, where it is cheap and legible.
- **Queries are not events.** Asking a question changes nothing and appends nothing — not to the log,
  and not to any local access record either. That file was considered and deliberately not built: it
  would be the one place the patient's own questions accumulate, inside a folder that syncs to a
  third party, and questions disclose more than the record does. Not behind a setting, because a
  setting someone can turn on is a file that can exist. A query must never create or modify a claim.
- **Answers are ephemeral.** Never written to `wiki/`. The wiki contains what artefacts support, not
  what the model once said about them.
- **Rejected content appears in no answer, under any phrasing.** Two mechanisms, both in code: the
  projection has already suppressed the claim, and any remaining free text carrying a rejected
  reading's own wording is withheld. Test it with a question aimed directly at rejected content, and
  assert the wording is in no byte of the request that leaves the machine — a model cannot leak what
  it was never given.
- **The box being unreachable degrades, it does not fail.** Retrieval is deterministic and runs with
  no network, so the answer becomes what the record holds, each entry cited. Unreachable and
  unauthorised stay different sentences here as everywhere else.

If this ever starts wanting a tool loop, that is a signal the retrieval layer is too weak, not that
the system needs a planner.

## Next: the `indication` predicate

Not part of phase 10 and deliberately not folded into it. Phase 10's job was to
make the query layer *honest* about not having this; reading it is an extraction
and projection change and it is its own piece of work.

**What it is.** Scripts and letters routinely say what a medication is for — "for
blood pressure", "to control your cholesterol", "for the reflux" — and it is the
first thing a clinician wants beside a drug name. Today the record holds the drug
and the dose and nothing that joins them to a reason, so "what am I taking for my
blood pressure" can only be answered with the list and a sentence saying the
record does not record what each is for.

**What it touches.** `agent/extract/schema.py` (`indication` joins `PREDICATES`),
the extraction prompt, `agent/projection/tiers.py`, the entity page and its
frontmatter, the consultation summary's medication lines, and the eval corpus —
the fixtures get expected indication claims, and nothing about a medication may
be scored wrong. The query layer needs no change at all: it already asks the
record whether it holds one and already says so when it does not.

**Rules it inherits, and one it needs.**

- **The model copies the literal span**, as everywhere else: `"for blood
  pressure"`, not a normalised condition and not a code. Render the literal.
- **An indication is not a diagnosis.** "For blood pressure" on a script does not
  put hypertension on the problems list, and must not be allowed to. A script
  says what was prescribed and why the prescriber wrote it; it is not a
  diagnostic assertion about the patient, and the two reach the record through
  different doors.
- **Linking an indication to a `problem:` entity is a merge-shaped decision**, so
  it needs a user tap and the same code-system-or-exact-match discipline merges
  get. A model matching "for blood pressure" to `problem:hypertension` is fuzzy
  semantic merging wearing a different hat.
- **The consequence tier has to be chosen deliberately.** It is not an add, a
  stop, a dose change or a diagnosis, so medium is the likely answer — but until
  it is written down, `tiers.consequence_for` fails closed and gates it high,
  which is the safe direction to be wrong in.

## Testing

- `test_rebuild_deterministic` — delete `wiki/` and `.agent/`, replay, assert byte-identical.
- `test_correction_survives_reextraction` — the correction wins regardless of event ordering.
- `test_high_consequence_never_autoapplies` — property test over generated claim streams; no
  confidence value and no elapsed time causes a high-tier claim to reach the wiki unconfirmed.
- `test_no_silent_drop` — a medication with evidence never disappears; it becomes `stale`.
- `test_conflicting_sources_render_both`.
- Fixture corpus of realistic artefacts in `tests/fixtures/`: a blurry phone photo of a script, a
  text-layer pathology PDF, a scanned specialist letter, a rambling 90-second voice note with a
  vague date. Synthetic, never real patient data.

## Things to ask before building

Do not decide these unilaterally.

- **Sensitive compartments.** Self-hosted on shared cloud storage means anyone with the Dropbox
  password reads everything. Mental health, sexual health, substance use and reproductive entries may
  need a separately encrypted compartment excluded from default exports.
- **Sharing.** Dropbox folder links are effectively unrevocable. Prefer generating a signed export
  over sharing live storage.
- **Emergency access.** The person who most needs the medication list is the one who is unconscious.
  A static offline card that regenerates when meds change is small and high-value.
- **Succession.** Who inherits the folder, and how a next-of-kin opens it without the app.

## Things not to build

- Any interface that answers **health questions** — as opposed to questions about the record. A 9B
  model confidently reassuring someone about chest pain is the worst outcome this project can
  produce. Querying your own record is a different thing and is phase 10; see "Querying the record".
- An agent framework, a tool-calling loop in the ingest path, or a planner. Ingest is a deterministic
  pipeline and must stay one — a nondeterministic ingest path breaks byte-identical rebuild, which
  every other guarantee depends on.
- Trend detection, risk scores, anomaly alerts, "you should see a doctor about this".
- Automatic sharing with practices or health services.
- Multi-user accounts, roles, or an auth system. Single user, localhost-bound. If it needs to be
  reachable elsewhere, that is the user's VPN problem, and binding to `0.0.0.0` is never a default.
