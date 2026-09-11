# MODELS.md — inference layer

Normative. `CLAUDE.md` defers to this file for anything model-related.

Two models run locally. Neither ever reaches the network beyond the local endpoint, and neither ever
writes to `wiki/`.

| Role | Runs on | Default | Purpose |
|---|---|---|---|
| Vision-language | **Remote** — MLX box over Tailscale | `Qwen3.8-Flash-Next` (oQ4e MLX) | Read artefacts, propose claims, draft summaries |
| Speech | **Local** — the laptop | `faster-whisper` `small` int8 | Voice notes → transcript |

**The split is deliberate.** ASR stays local because it is small (~500 MB), because voice notes are
the most time-sensitive capture and must work in a waiting room with no tailnet, and because it
avoids shipping audio over the wire at all. The VLM moves to the box because it is the part that
actually needs the hardware, and because moving it removes model contention from the laptop entirely.

Assume the endpoint is unreachable a good fraction of the time — the box sleeps, the laptop drops off
the tailnet, you're on a plane. Everything below follows from that.

## Vision-language model

`Qwen3.8-Flash-Next` is a natively multimodal MoE with a vision encoder — 125B total parameters with
6B activated per token, plus a 51B n-gram embedding table and a 4B MTP head, 262K native context.
It reads a photographed script directly rather than consuming OCR output. Licence is the Qwen
Community License 1.0, not Apache-2.0: free to use and deploy commercially, but with naming clauses
above 100M MAU or $20M monthly revenue and a separate licence for Model-as-a-Service. Irrelevant for
a personal record; relevant if this ever ships.

Two server settings are not optional for this project:

- **Thinking off, or a reasoning parser set.** These models think by default and emit
  `<think>…</think>` before the answer. With no reasoning parser, that lands in `content` and every
  schema-validated extraction fails. Extraction does not benefit from the trace anyway.
- **Guided grammar on.** This is the schema-constrained decoding the claim path depends on.

**SSD n-gram offload keeps the model in memory but slows prefill after context changes**, and vision
prefill is exactly this project's workload. If extraction is slow, test with it off before
optimising anything else.

Keep `Qwen3.5-4B` (Apache-2.0, GGUF) configured as the local offline fallback. Do not go below 4B
there: the 2B and 0.8B variants will load on anything and produce fluent wrong doses, which is the
one output this project cannot tolerate.

**Verify vision actually works at startup.** The client speaks plain OpenAI
`/v1/chat/completions` with `image_url` content parts. Support for those parts is less uniform across
MLX servers than across llama.cpp — `mlx_lm.server` is text-only; you want an `mlx-vlm` server, or
whatever your box runs, and it must accept image content parts rather than silently discarding them.
Probe at startup with a fixture image containing known text and fail loudly if the response doesn't
contain it. The failure mode otherwise is a model that answers text prompts perfectly and quietly
ignores every prescription photo, which reads as "the model is bad at OCR".

The same probe covers the llama.cpp offline path, where the equivalent trap is loading the weights
without the separate `mmproj` projector file.

### Runtime

The client speaks only OpenAI-compatible `/v1/chat/completions` against a configured base URL. That
is the entire coupling. Whether the far end is `mlx-vlm`, `llama-server`, vLLM or Ollama is not the
client's business, and no model-specific code exists outside `agent/llm/client.py`.

### Endpoint safety

An `openai_base_url` config field is a foot-gun: paste `https://api.openai.com/v1` and a key, and the
entire privacy premise of the project evaporates with no visible change in behaviour. Guard it.

- **Allowlist private address space at startup.** Resolve the host and require it to fall in
  `100.64.0.0/10` (Tailscale CGNAT), RFC1918, or loopback. Anything else is a startup failure with a
  clear message — not a warning, not a toggle buried in settings.
- **Resolve and check on every call, not just at boot.** A hostname that resolved privately at
  startup can resolve elsewhere later. MagicDNS names (`host.tailnet.ts.net`) are fine — they resolve
  to `100.x` — and HTTPS via Tailscale Serve carries a real certificate, so TLS verification stays
  on. Never disable it.
- **The address guard cannot detect Tailscale Funnel.** A Funnel-exposed endpoint still resolves to
  `100.x` from inside the tailnet while being reachable from the public internet. Nothing in this
  codebase can see that, so it is an operational rule rather than a check: the inference endpoint is
  served with `tailscale serve`, never `tailscale funnel`. Say so in setup docs and verify with
  `tailscale serve status` when things look wrong.
- **Refuse plaintext credentials to a public host** under any override. If someone genuinely wants a
  commercial API they can fork the project; the guard is not a UX obstacle to be smoothed away.
- **Authenticate anyway**, and put a Tailscale ACL in front of the box. Every device on your tailnet
  should not be able to query your health record's inference endpoint. See "Credentials" below.
- **Validate the host before attaching credentials.** Resolve, check the address range, and only then
  build the auth header. If the guard ever fails open, this ordering means the key still doesn't
  leave the machine.

### Credentials

The MLX server requires an API key. Handle it as follows.

**Never in `config.toml`.** That file lives at the vault root and syncs to Dropbox, Drive or
Nextcloud — a key written there has been uploaded to a third party, which is precisely the thing this
project exists to avoid. `config.toml` holds only a reference to where the key lives, never the key.

**Resolution order**, first hit wins, fail closed with a clear message if none:

1. Environment variable named by `api_key_env` (useful for headless and systemd runs)
2. OS keychain via `keyring` — macOS Keychain, Windows Credential Manager, Linux Secret Service —
   under service `health-agent`, account `vlm-endpoint`
3. A file at `~/.config/health-agent/credentials`, mode `0600`, **outside the vault**. Refuse to read
   it if the mode is wider, and refuse if the resolved path is inside the vault root.

Keychain is the default and what first-run setup writes to.

**Header shape is configurable.** Default `Authorization: Bearer <key>`, but some self-hosted servers
expect `X-API-Key`. Put `auth_header` and `auth_scheme` in config so a server change doesn't need a
code change.

```toml
[models.vlm.auth]
api_key_env = "HEALTH_VLM_TOKEN"   # optional; keychain used if unset
header      = "Authorization"
scheme      = "Bearer"
```

**The browser never sees the key.** The SPA talks to FastAPI on localhost; FastAPI talks to the box.
The key must never appear in any API response, in `/api/health`, in an error payload returned to the
frontend, or anywhere in frontend JS. `/api/health` reports `auth: ok | failed | missing` and nothing
more.

**Redact everywhere.** Install a logging filter that scrubs the key value from every log line and
exception message before it is written. `httpx` will happily include request headers in some error
representations, and the event log records endpoint provenance — neither may ever contain the
secret. Test this directly rather than assuming.

**Set-only in the UI.** A settings field shows `•••• configured` or `not set`, accepts a new value,
and has no read path. There is no endpoint that returns the current key.

**Rotation without restart.** Read from the keychain per call rather than caching at boot, or support
a reload signal. A rotated key should mean one failed job, not a restart and a confused user.

### Auth failures are not network failures

This distinction matters more than it looks. A `401` or `403` is a **terminal** condition for the
queue, not a transient one:

- Do not retry with backoff. Retrying a rotated key fifty times achieves nothing and may trip
  rate limiting or lockout on the server.
- Park the queue immediately, mark jobs `blocked-auth`, and surface a specific message:
  "authentication rejected by the inference box — the key may have rotated." Not "processing failed."
- Distinguish clearly in the UI between **unreachable** (box asleep, off tailnet — retry silently,
  drain later) and **unauthorised** (needs the user to do something). Collapsing them into one
  "offline" state means a rotated key looks like a sleeping Mac and nobody investigates for a week.
- On startup, probe with a cheap authenticated request so all three states — unreachable,
  unauthorised, working — are known before the first real job runs.

A `429` is its own case: back off and retry, but log it, because a self-hosted box rate-limiting you
usually means something else is hammering it.

### Network behaviour

Everything here follows from the endpoint being frequently unreachable.

- **Capture never depends on the endpoint.** Artefacts land in `raw/` and the job queue persists to
  `.agent/jobs.jsonl` regardless. A capture that fails because a Mac was asleep is unacceptable.
- **Generous read timeouts.** Vision prefill on a large page can exceed 60 s. A default httpx timeout
  will kill live requests and look like a flaky endpoint. Use a short *connect* timeout and a long
  *read* timeout — they are different problems.
- **Health check separate from job execution.** Poll the endpoint cheaply, show reachability in the
  UI ("box offline — 6 items queued"), and don't discover unreachability by failing a 90-second job.
- **Retries must be idempotent.** A retried extraction must not emit a second `claim.proposed`. Key
  each job on `(artifact_hash, prompt_hash, model_id)` and make emission conditional on that key not
  already appearing in the event log. Retry with backoff, cap attempts, then park the job as
  `needs-attention` rather than retrying forever.
- **Downscale for bandwidth, not just memory.** Base64 in JSON inflates by a third, and you are
  pushing it over WireGuard. The image budget below still applies with full force.

### Model identity

A remote server can be swapped under you without the client noticing, which would silently corrupt
provenance. Read the `model` field the server returns and record it in every
`extraction.completed` event. If it disagrees with `config.toml`, **stop and surface it** — do not
proceed and do not auto-update the config. "Which model produced this claim" must be answerable a
year later, and a config file describing a server's past state is worse than no record.

Because the box's model is not content-addressable the way a local GGUF is, keep a
`models.registry` table in the event log: an event recording each observed model identity string and
when it was first and last seen.

### Local fallback

Optional, off by default, and worth building only after the remote path works: `Qwen3.5-4B` at
`UD-Q4_K_XL` via llama.cpp on the laptop, for extraction while away from the tailnet. If built, it
carries its own model identity in provenance and its claims are marked `degraded-tier` so a later
re-extraction against the 9B is easy to find and diff.

The single-slot constraint only applies in that configuration. In the normal split — ASR local, VLM
remote — nothing contends, and Whisper `small` at ~500 MB is the laptop's entire model footprint.

### Context

Native context is 262K, extensible past 1M. **Do not use it.** Small models degrade badly on long
context regardless of the advertised window, and a 200K-token prompt on a 9B model will produce
confidently wrong reconciliation. Cap the prompt at 8–16K and retrieve narrowly: the artefact, the
2–3 wiki entities it plausibly touches, nothing else. Never dump the whole wiki into a prompt. If a
prompt exceeds the cap, that is a retrieval bug, not a reason to raise the cap.

### Image token budget

**This is the most likely cause of an out-of-memory crash, and it is not obvious.** Qwen's vision
encoder tokenises by pixel count. A 12-megapixel phone photo of a prescription becomes thousands of
vision tokens, blowing past the context cap and the RAM budget before the model has generated a
single output token. The image, not the prompt, is what kills you.

Preprocess every image before it reaches the model:

- Downscale so the long edge is ~1280 px on `constrained` (~1600 on higher tiers), preserving aspect
  ratio. Set `max_pixels` explicitly on the request rather than relying on server defaults.
- Deskew, autocrop to the document, and convert to greyscale where it does not lose information —
  smaller and easier to read.
- **Never send the original.** The original stays in `raw/` untouched; the model sees a derived
  working copy that is never persisted to the vault.
- For PDFs, render pages at 150 DPI, not 300. Higher DPI does not improve VLM reading the way it
  improves tesseract, and it costs quadratically.
- If a document is unreadable after downscaling, that is a "could not read — review manually"
  outcome. Do not escalate resolution until it fits; it won't.

Log vision token counts per artefact. A sudden spike is the signal that preprocessing was bypassed.

**One page per prompt** for multi-page PDFs, always. Never concatenate pages — it inflates the image
budget, degrades reading accuracy, and destroys per-page citation granularity.

### Design constraints that survive the upgrade

Moving to 9B on a dedicated box relaxes the latency picture, not the trust picture. Keep these:

- **Conflict adjudication stays with the human.** Contradictions are surfaced with both readings and
  their sources. A 9B model can write a persuasive paragraph about which dose is more likely, and
  that paragraph is exactly the kind of fluent unverifiable output this project is built to avoid.
- **Merge proposals require a code-system match** (ATC/RxNorm) or an exact normalised string before
  the model is consulted. Fuzzy semantic merging stays off.
- **Consultation summaries are a pure function of the record — no model call at all.**
  Structure, section order and selection rules are code, and the model writes nothing. An earlier
  draft allowed it "short connective prose"; that was wrong. Model prose is the one kind of sentence
  on the sheet that cannot carry a citation to an artefact, and it spends a hard one-page budget on
  text that adds no fact. This is settled architecture, not a phase-scoping choice: no later phase
  "finishes" the feature by adding the prose back.

These were framed as concessions to a 4B model in an earlier draft. They aren't — they're the
architecture. A better model makes them easier to abandon and no more correct to abandon.

### Latency expectations

Still asynchronous, just faster. On an Apple Silicon box, 9B 4-bit runs comfortably and vision prefill
dominates: a photographed script is plausibly 10–30 seconds end to end, plus tailnet round trip.
Whisper `small` locally on a 90-second voice note is roughly 20–40 seconds.

Set UI copy accordingly, and never build a screen that waits for a result. The queue is also now
frequently *paused* rather than slow, which is a different message: "box offline — 6 items queued"
is informative, a spinner is not.

### Thinking mode

Toggleable. Disable it for routine extraction — it roughly doubles token count and latency for no
accuracy gain on "read this script". Enable it for the few tasks where the reasoning trace is worth
storing in the `extraction.completed` event, so a human reviewing a contradiction can see why the
model proposed what it did. Note that enabling thinking never grants the model authority to *decide*
a conflict; the trace is evidence for the reviewer, not a verdict.

### Structured output

Constrain decoding to a JSON schema — GBNF grammar in llama.cpp, `format` in Ollama. Do not
post-process free text into JSON with a regex.

**Validate, then reject. Never repair.** A malformed claim that gets coerced into a valid one is how a
dose becomes wrong silently. Log the raw output, emit no claim, surface the artefact as "could not
read — review manually".

**Sampling for extraction is not the model card's recommended sampling.** Those settings are tuned
for prose and agentic work. Extraction wants near-zero temperature for reproducibility, and the
non-thinking recommendation of `presence_penalty=1.5` is actively harmful here — JSON keys repeat by
design, and penalising repeated tokens fights the grammar. Pin low temperature, no presence penalty,
and record the sampling parameters in the `extraction.completed` event alongside the prompt hash.

**The model never does arithmetic or date math.** It extracts literal strings — `"30 tablets"`,
`"twice daily"`, `"since around Easter"`, `"1 repeat"` — and Python parses them deterministically.
Every quantity in the wiki must be traceable to a literal span the model copied, not a number it
computed. This applies to expected-exhaustion dates, dose totals, and date normalisation without
exception.

**The model never assigns a consequence tier.** Tier is a lookup on the predicate (`allergy.*` and
`medication.*` are high, always). If the model could label something low-consequence, a bad
extraction could route itself around review.

## Dual-path extraction

Every document artefact goes through two independent readers:

1. **Deterministic** — PDF text layer via `pdfplumber` where one exists, `pytesseract` otherwise.
2. **VLM** — Qwen3.5 reading the image or rendered page directly.

Try the text layer first for PDFs; pathology reports from patient portals almost always have one and
running OCR over the rendered page instead makes them worse.

Then compare. Where both agree on a drug name, dose or value, the claim carries `cross-verified` and
can follow normal tier rules. **Where they disagree on anything high-consequence, do not pick a
winner** — emit the claim as `conflicted` with both readings attached and force human review. Two
independent readers disagreeing about a dose is exactly the signal you want, and averaging it away or
trusting the more fluent one defeats the point of the architecture.

Tesseract on a blurry phone photo of a handwritten script is close to useless on its own, so treat
the deterministic path as a check on VLM hallucination rather than as a reader in its own right. The
VLM is far better at reading; it is also the one that will invent a plausible dose that was never on
the page.

## Speech

`faster-whisper` (CTranslate2), `small` with `compute_type=int8` on the constrained tier. Roughly
500 MB resident, and it beats `base` on medical vocabulary by enough to matter. Do not drop to
`tiny` — the drug-name error rate makes the transcripts actively misleading rather than merely rough.

Because `small` is weaker than `medium`, the biasing below is not an optimisation on this tier. It is
what makes the transcripts usable.

### Biasing on the record you already have

Whisper mangles drug names — "perindopril" comes back as "peran doprol", "frusemide" as "for
semide" — and a mangled drug name is an unmatched entity, which becomes a duplicate wiki page.

You already have the answer. Before each transcription, pull current medication names, allergy terms,
active problem names and practitioner names from the wiki and pass them as `hotwords` / biasing
context. This is the single highest-leverage thing in the ASR path and costs nothing. Cap the list —
a few dozen terms, most recent first — because an overlong bias prompt makes Whisper start inventing
those words in silence.

### Settings that matter

- **Pin the language.** `language="en"` (with the user's locale in config). Auto-detection on a
  90-second clip with background noise picks Welsh surprisingly often.
- **VAD filter on.** Whisper hallucinates on silence, typically producing subtitle-corpus artefacts
  like "Thank you for watching". Filter silence, and additionally drop segments with high
  `no_speech_prob` or a compression ratio above threshold. Never let a hallucinated segment become a
  claim.
- **Word-level timestamps on.** Store them in the transcript. This lets a claim cite not just "voice
  note 2026-09-02" but the exact 4-second span it came from, and the review UI can play that span.
  Citation granularity at the second is a genuine feature here, not polish.
- **Keep the audio forever.** The transcript is derived and re-derivable; the recording is the
  artefact. Never delete audio after transcription, even on re-transcription with a better model.

Re-transcription is an `extraction.completed` event like any other, subject to the same rule that
user corrections outrank it.

## Model registry and re-derivation

`config.toml` pins exact artefacts, never floating tags:

```toml
[models.vlm]
base_url   = "https://macbook-pro.tailb017fc.ts.net/v1"   # MagicDNS; resolves to 100.x
model      = "Jundot/Qwen3.8-Flash-Next-oQ4e-mtp"         # copy verbatim from /v1/models
ctx        = 16384
max_pixels = 1638400                      # 1280x1280
connect_timeout_s = 3
read_timeout_s    = 180
temperature       = 0.0
presence_penalty  = 0.0

[models.vlm.auth]                         # reference only — never the key itself
api_key_env = "HEALTH_VLM_TOKEN"
header      = "Authorization"
scheme      = "Bearer"

[models.asr]
name         = "faster-whisper-small"
compute_type = "int8"
sha256       = "..."

[models.vlm_fallback]                     # optional, off by default
enabled = false
model   = "Qwen3.5-4B"
quant   = "UD-Q4_K_XL"
sha256  = "..."
mmproj_sha256 = "..."
```

Local artefacts are pinned by `sha256`. The remote model cannot be, so it is pinned by the identity
string the server reports and verified on every call.

Every `extraction.completed` event records the model name, quant, both hashes, and the prompt hash.
When a model is swapped, `POST /api/rebuild --reextract` re-runs everything and produces a **diff
report**, not a silent overwrite: claims that changed, claims that appeared, claims that vanished.
Anything touching a confirmed or corrected claim is presented for review. Re-extraction never
overrides a `claim.corrected`.

`sha256` on model files is not paranoia — it is what makes "why did the record change last Tuesday"
answerable a year later.

## Eval harness

`tests/fixtures/` holds the golden corpus with hand-written expected claims. Run it on every model or
prompt change, before the swap is accepted.

**Recall on medications, doses and allergies must be 100%.** A false positive gets caught in review; a
missed medication is invisible and is precisely the failure this project exists to prevent. Report
recall and precision separately and never trade recall for precision.

**Run the corpus on both tiers** if the local fallback is built. The 4B and 9B results go in the same
report. If 4B recall on medications, doses or allergies falls below 9B on any fixture, that fixture
becomes a required review case at the fallback tier — the answer is to route it to a human, never to
quietly accept the worse result because the box was unreachable.

Three tests belong here rather than in the fixture corpus:

- `test_endpoint_guard_rejects_public_host` — a public hostname or a commercial API base URL fails at
  startup, and fails again on a host that re-resolves publicly mid-session.
- `test_capture_survives_offline_endpoint` — with the endpoint refused at the socket level, capture
  succeeds, the artefact lands in `raw/`, the job persists, and it drains on reconnect.
- `test_retry_is_idempotent` — a job retried three times across a restart produces exactly one
  `claim.proposed` per claim.

And four on credentials, which are cheap and catch the failures that matter most:

- `test_key_never_written_to_vault` — walk the entire vault after a full run and assert the key value
  appears in no file, including `config.toml`, the event log and `.agent/logs/`.
- `test_key_redacted_from_logs_and_errors` — force an httpx error carrying request headers and assert
  the key is scrubbed from the log line, the exception message and any response to the frontend.
- `test_auth_failure_parks_queue` — a `401` marks jobs `blocked-auth`, does not retry, and reports a
  distinct state from unreachable.
- `test_credentials_never_reach_frontend` — no route, including `/api/health` and error handlers,
  returns the key or any prefix of it.

Fixtures to include, all synthetic, never real patient data:

- Blurry phone photo of a handwritten script, taken at an angle
- Printed script with two medications and a repeat count
- Text-layer pathology PDF with an out-of-range flag
- Scanned specialist letter, skewed, with a letterhead
- A 90-second rambling voice note with a vague date and two drug names
- A voice note that is 40 seconds of silence and a cough — expected output: nothing
- Two artefacts giving contradictory doses for the same drug — expected output: `conflicted`
- An artefact in the user's second language, if applicable

## What the model is never allowed to do

- Decide reconciliation outcomes. It proposes; deterministic rules in `agent/projection/` decide.
- Compute any number that reaches the wiki.
- Assign consequence tiers.
- Write to `wiki/`, `events/`, or `raw/`.
- Answer a health question, suggest a cause, or characterise a trend as concerning.
