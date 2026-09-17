# MODELS.md — inference layer

Normative. `CLAUDE.md` defers to this file for anything model-related.

Two models. Neither ever reaches the network beyond an endpoint on a machine the user owns, and
neither ever writes to `wiki/`.

| Role | Runs on | Default | Purpose |
|---|---|---|---|
| Vision-language | **This computer** (default) — a managed `llama-server` on loopback | `Qwen3.5-4B` Q4_K_M + mmproj | Read artefacts, propose claims |
| Vision-language | **Another computer** (a choice) — MLX box over Tailscale | `Qwen3.8-Flash-Next` (oQ4e MLX) | The same, faster |
| Speech | **This computer**, always | `faster-whisper` `small` int8 | Voice notes → transcript |

**Where documents are read is a choice per machine, not per vault.** See "Where documents are read"
below. Installing the app and dragging in a photo must work with no endpoint, no key and no
Tailscale; a fast box is an option for people who have one, not a prerequisite.

**Speech stays local whichever reader is chosen.** It is small (~500 MB), voice notes are the most
time-sensitive capture and must work in a waiting room with no tailnet, and keeping it local avoids
shipping audio over the wire at all.

Assume a remote endpoint is unreachable a good fraction of the time — the box sleeps, the laptop drops
off the tailnet, you're on a plane — and assume the local reader is asleep a good fraction of the time
too, because it unloads when idle. Everything below follows from that.

## Where documents are read

Two options, chosen on each machine, stored at `~/.config/health-agent/reader` beside the device
identity and **never in `config.toml`**. That file syncs to every machine, so "read on this computer"
written from the laptop would be a false statement on the desktop — the same reasoning that put device
identity outside the vault. The remote endpoint's details stay in `config.toml`, because the box is the
same box from every machine.

- **Read on this computer** — the default for a new vault. A `llama-server` binary and the
  `Qwen3.5-4B` weights, managed by the app. Nothing to configure.
- **Read on another computer** — the endpoint path described in the rest of this file.

An existing vault with a `[models.vlm]` table starts on **another computer**, so upgrading changes
nothing for someone already reading on a box. The default is written to the per-machine file the first
time the server starts, so it cannot silently flip later because a synced config gained a table.
A demo vault is not an exception: it reads documents wherever this machine does. The reader's files
belong to the machine, not the vault, so a download made for a demo is the same download a real vault
uses and outlives the demo being deleted; a demo can already reach a remote box, so refusing only the
local path would track nothing; and invented documents read by a local model are the only way to see
extraction work end to end without a personal document anywhere. Two things do stay: a demo never
*writes* this machine's default (it has no `[models.vlm]` table, so it would record "this computer"
over a real vault's "another computer"), and it keeps saying it is a demonstration, because claims
extracted from invented documents are still invented.

There is **no automatic fallback** between the two. A job waits for the reader the machine chose. A
smaller model quietly standing in because the box was asleep is exactly the substitution that made a
"degraded" marker necessary, and it is not built.

### The bundled reader

`agent/llm/` does not know it exists. The client speaks OpenAI HTTP to a base URL and the address guard
permits loopback, so the bundled reader is a process at `http://127.0.0.1:{port}/v1` and the client
is handed settings pointing at it.

**Nothing is committed to the repository and nothing is compiled on the user's machine.** The
`llama-server` binary is downloaded like the weights: pinned to one llama.cpp build per platform, by
exact size and sha256, from a manifest in `agent/runtime/manifest.py`. Git history is forever and the
project's durability claim is about the vault, not the repo. `llama-cpp-python` is not used — bundling
the binary keeps the OpenAI-HTTP seam intact.

| Platform | Binary | Verified in development |
|---|---|---|
| Linux x64 | `llama-…-bin-ubuntu-x64.tar.gz` | yes |
| macOS arm64 | `llama-…-bin-macos-arm64.tar.gz` (Metal) | **no** |
| macOS x64 | `llama-…-bin-macos-x64.tar.gz` | **no** |
| Windows x64 | `llama-…-bin-win-cpu-x64.zip` | **no** |

The unverified rows are pinned and untested, and say so. Code existing for a platform is not evidence
that it works there.

**Weights and binaries live outside the vault** — `$XDG_DATA_HOME/health-agent`, `~/Library/Application
Support/health-agent`, or `%LOCALAPPDATA%\health-agent`. A data directory that resolves inside the vault
is refused: 3.6 GB of model would sync to Dropbox.

### Downloading

The one-time download is the only outbound connection this app makes other than to an inference
endpoint the user configured. It is therefore held to rules of its own:

- **Never automatic, and never unasked — on every vault.** It starts from an explicit confirmation that
  names the exact bytes still to fetch (from the manifest and what is already on disk, not an
  estimate), which hosts it contacts, where the files go, and that no part of the record is sent. The
  server enforces it: `POST /api/reader/download` must carry `confirm_bytes` equal to what it would
  fetch right now, and anything else is refused with the real figure. An unexpected multi-gigabyte
  download is the harm, and it is guarded by asking, not by deciding which vaults may ask. An
  interrupted download says "Continue" and asks again.
- **Allowlisted hosts, https only, checked on every redirect hop.** Hugging Face and GitHub release
  assets and their CDN hosts, and nothing else.
- **Resumable.** A `.part` file and a `Range` request; a server that ignores `Range` restarts from zero
  rather than appending the wrong bytes.
- **Verified before use.** sha256 over the whole file, then an atomic rename. A mismatch deletes the
  partial file and says so; there is no retry loop. A later start re-hashes a file whose size or
  modification time has changed since it was verified.
- **Space and memory are checked first.** Not enough free disk is a refusal before a byte is fetched.
  Under 8 GB of total RAM is a warning that names the remote option — not a refusal.
- **"I already have the files" is supported** by placing them in the data directory; they are
  verified exactly as a download would be. An air-gapped machine is a legitimate configuration.

### Running it

One `llama-server` per machine, owned by a lock in the data directory. A second process that wants the
reader while the server holds it is refused with a sentence, rather than loading a second 3.5 GB copy.

- **Flags are not left to defaults.** `--host 127.0.0.1`, a free port, `--parallel 1` (the default
  splits the context across slots and silently shrinks each request's room), `--jinja` (the thinking
  toggle is a template kwarg), `--no-webui`, `--no-slots`, `--offline`, `-c` from `models.vlm.ctx`, and
  `--alias` set to `qwen3.5-4b-q4_k_m@{first 12 of the weights sha256}`, so the identity string changes
  exactly when the bytes do.
- **Not port 8080.** It is llama-server's default and the most contended port on a developer's machine;
  another llama-server may well be answering there.
- **A key, even on loopback.** Any web page in any browser tab can send a request to `127.0.0.1`, and
  llama-server answers cross-origin. So each launch gets a fresh random key, handed to the child through
  `LLAMA_API_KEY` — never on the command line, where `ps` shows it to every user — and never written
  anywhere. It reaches the client through an explicit credential, never through the resolver chain,
  which would fall through to the keychain and send the *remote* box's key to whatever is on the port.
- **Identity before the key.** In the pinned build only `/health` answers without a key — `/v1/models`
  and `/props` do not — so identity before the key is *process* identity, not something a port says
  about itself: the child this app spawned is still alive, its own output says it is listening on the
  chosen port, and on Linux the listening socket on that port belongs to the child's pid. Only then is
  the key released to the client. Anything else answering on that port is refused having been sent
  nothing. After the key, the first authenticated request must report the pinned alias as `model` and
  the pinned build as `system_fingerprint`; a disagreement stops the reader. On macOS and Windows the
  socket-ownership check is not available and the other two stand alone.
- **The full startup probe runs on the first start in a process**, grammar and vision both. Weights
  without the projector load fine, answer text fine, and ignore every image. A wake from sleep, with
  the same verified files and the same flags, checks `/props` reports vision among its modalities
  rather than paying for a second image read.
- **Crashes restart with backoff**, and three within ten minutes stop the reader with a state saying
  so and naming the log file, rather than restarting for ever. A child killed by a signal is reported
  as most likely out of memory.
- **It sleeps when idle** — after 15 minutes by default, configurable per machine — and never while a
  job is queued or a request is in flight. The state says "sleeping — wakes when you add something",
  because a first read that takes ten seconds longer than the next one must not look like a fault.
- **It stops when the app stops.** On Linux the child is also told to die with its parent; on Windows it
  is placed in a kill-on-close job object; on macOS, which has neither, a pidfile lets the next start
  stop an orphan after checking it is this app's binary.
- **Its log is outside the vault**, in the data directory, and is never run verbose: verbose output
  includes prompts.

### Choosing the model

The reader on this computer offers a list of models from the manifest — today Qwen3.5-4B Q4
(recommended, preselected, runs on 8 GB) and Qwen3.5-9B Q4 (16 GB). Adding one is a manifest entry and
a measurement, never a code path.

**The person chooses knowing.** The 4B model does not meet the eval bar: on the blurry handwritten
script it read metformin as mefenamic acid, with a dose, and did not abstain — a wrong fact that makes
no review item, so nothing in the app can catch it. It ships as the recommended local reader anyway,
because local reading only means anything on the laptop most people own, and a larger model does not
fit alongside Windows in 8 GB. What makes that acceptable is disclosure at the point of choice, not that
it was quietly good enough. So every entry shows, in the list itself: download size, memory needed,
speed on this computer (its own readings, or a measurement on a named machine, or "not measured"), and
correct / left for you to check / wrong on medications, doses and allergies from the eval corpus (or
"not measured"). A failure a person found that the app cannot catch is said beside its model, in plain
words, with what to do about it.

**Measurements are data, recorded from real runs.** `agent/runtime/measurements.json`, keyed by the
model's identity string (so re-pinned weights start unmeasured), written by `health-agent eval
--record` and committed. Known failures are written by a person and kept across re-records.

**Memory is warned about, never enforced.** A model needing more than the machine has gets a sentence
saying what will happen — slow, other programs squeezed, possibly stopped part-way — and can still be
chosen.

**What read each claim is shown beside its evidence tier** — "read by Qwen3.5-4B on this computer" —
from the event's provenance, never merged into the tier.

### Honest about speed

A 4B model at Q4 on a laptop CPU is slow, and slower than first assumed. Measured in development on a
Core Ultra 7 155U: a photographed page at the 1280 px budget is about 1,650 prompt tokens, read at
16 tokens a second with every thread in use — about 100 seconds before the answer starts, and several
minutes a document in all. The pre-measurement estimate is therefore "about 1 to 4 minutes a document"
on a processor, and "about 10 to 30 seconds" on Apple Silicon with Metal, which has **not** been
measured. Once this machine has read something the interface says what it actually took — "usually
about 2 minutes 30 seconds a document on this computer, 6 waiting". A queue that is slow must never
look like a queue that is broken.

**The read timeout follows the ceiling.** One timeout for the whole ladder meant the top rung —
8,192 tokens at about 9 tokens a second — could not finish, and a reader still writing was reported as
a box that had gone to sleep. Each request's read timeout is the configured allowance for reading the
page plus the ceiling at the slowest generation speed that model's own server has reported (or its
measured speed, or 2 tokens a second), with half as much again. A cut-off answer that ends in the same
passage repeated stops climbing — more room only buys a longer repetition — and is parked as a run-on,
saying so. A timeout from the reader on this computer is parked the same way; a remote box's timeout is
still unreachable, because it may genuinely have gone to sleep.

**Threads are stated, never left to llama-server's default.** On hybrid Intel laptop chips the default
counts only performance cores — 2 of 14 threads on the machine above, which doubled the time. The
reader uses all logical CPUs but two for generation, and all of them for reading the prompt.

A question asked of the record goes before the next queued document, not behind the whole queue. It can
still wait for the document being read, and the screen says that too.

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

`Qwen3.5-4B` (Apache-2.0, GGUF) is the bundled reader — see "Where documents are read". Do not go
below 4B there: the 2B and 0.8B variants will load on anything and produce fluent wrong doses, which is
the one output this project cannot tolerate.

**Verify vision actually works at startup.** The client speaks plain OpenAI
`/v1/chat/completions` with `image_url` content parts. Support for those parts is less uniform across
MLX servers than across llama.cpp — `mlx_lm.server` is text-only; you want an `mlx-vlm` server, or
whatever your box runs, and it must accept image content parts rather than silently discarding them.
Probe at startup with a fixture image containing known text and fail loudly if the response doesn't
contain it. The failure mode otherwise is a model that answers text prompts perfectly and quietly
ignores every prescription photo, which reads as "the model is bad at OCR".

The same probe covers the llama.cpp offline path, where the equivalent trap is loading the weights
without the separate `mmproj` projector file.

**Probe guided grammar at startup too, for the same reason.** Some servers apply schema-constrained
decoding only through their own toggle and accept `response_format` without acting on it. The effect
is every extraction failing on a parse error that points at the schema, which is the wrong place to
look. Send one tiny schema-constrained request — a single-field object — and assert three things: it
parses, it is the shape it was given, and `finish_reason` is `stop` rather than `length`. The tiny
ceiling is part of the check: a one-field object cannot run past sixty-four tokens unless nothing is
constraining it. This runs before the vision probe, because it is cheaper and because a server that
ignores the schema explains a good deal else besides.

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

### Recording which reader read it

An earlier draft specified a local *fallback* whose claims were marked `degraded-tier`. That label is
retired, and it is not replaced by another label. **Record the facts, derive the judgement.**
"Degraded" is relative to what other readers exist, which changes, and a line in an append-only log
can never be revised — it is a quality assessment frozen at write time, which is the same mistake as
trusting the consequence tier a payload carries instead of recomputing it from the predicate.

So every `extraction.completed` carries a `runtime` block of observations:

| Field | Bundled reader | Another computer |
|---|---|---|
| `kind` | `bundled` | `endpoint` |
| `engine` | `llama.cpp b10997` | `null` |
| `files` | `{filename: sha256}` for weights and projector | `null` |
| `elapsed_s` | wall time for the whole artefact | the same |

`kind` names *what* ran, never *where relative to the reader of the log*. "This computer" is false on
every other machine that syncs the folder; `bundled` plus the event's own `device` is true everywhere.
`claim.proposed` provenance carries `runtime_kind`, so a claim says what read it without a join. The
review item and the entity's source list show it — "read by Qwen3.5-4B on elwood-laptop" — separately
from the evidence tier, which describes the document and never the reader. It is not on the
consultation summary.

Whether the default reader is good enough is answered by the eval corpus, not by a label: the bundled
reader meets the wrong-is-zero bar in its own right before it ships as the default.

Speech and the bundled reader do not read at the same time in the background worker: a pass
transcribes what is waiting first, and only then asks the reader for anything.

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

**A page is read in groups, as turns of one conversation.** Medications first, with the page attached;
then allergies; then conditions and practitioners. Every group's schema has an `unclear` list, and the
prompt's first rule is that unsure means unclear — a small model asked to fill a schema will fill it,
so leaving something out has to be a place in the schema, not an absence. Measured on the bundled
reader, a follow-up turn reuses the page from llama-server's prompt cache (about 3 seconds against
about 80 for the first), while the same question as a separate prompt re-pays the image prefill. So
groups are turns; as separate prompts the idea would cost three times the prefill and would not be
worth it. The sequence is fixed — this is not a loop, and the model's answers decide nothing about
what is asked next beyond not asking about a page it said it cannot read.

**A medicine's name, strength and frequency are separate fields.** A name carrying a digit, unit or
form is refused, not trimmed. A dose is proposed only when strength and frequency were both read; one
without the other is an abstention, because a dose without its frequency is not the dose on the page.
Every refusal of this kind is recorded as an abstention and reaches the review queue.

**Validate, then reject. Never repair.** A malformed claim that gets coerced into a valid one is how a
dose becomes wrong silently. Log the raw output, emit no claim, surface the artefact as "could not
read — review manually".

**Sampling for extraction is not the model card's recommended sampling.** Those settings are tuned
for prose and agentic work. Extraction wants near-zero temperature for reproducibility, and the
non-thinking recommendation of `presence_penalty=1.5` is actively harmful here — JSON keys repeat by
design, and penalising repeated tokens fights the grammar. Pin low temperature, no presence penalty,
and record the sampling parameters in the `extraction.completed` event alongside the prompt hash.

**`finish_reason: "length"` is its own failure, never "malformed output".** They arrive at the
parser looking identical — JSON that will not load — and they want opposite investigations. "The
model ran out of room" is a ceiling to raise; "the model produced invalid output" is this server's
guided decoding to check. Reporting the first as the second sends someone to read documentation
about grammar settings while a token cap sits there unexamined. Carry `finish_reason` on every
completion, check it **before** anything parses the content — a server can stop mid-array and leave
something structurally valid but short, and the claims it dropped are the ones nobody would notice
were missing — and record it on the `extraction.completed` event.

**The output ceiling is raised on evidence, not guessed and not configured.** A prescription yields
three claims; a pathology report yields four result tables and a comment, and one fixed number
cannot be right for both. So the first request gets an ordinary budget, and a cut-off answer is
asked again with more room: double each time, stop at a hard ceiling, and clamp every step to what
`ctx` can still hold given the prompt tokens the server itself reported. There is no `max_tokens`
config key — the person who would have to find and raise it is exactly the person who does not yet
know that a token cap is what they are looking at.

A page that still overruns at the top of the ladder is **reported, never half-read**. One page per
prompt is already the rule and sectioning a single page would destroy per-page citation granularity
for two half-answers nothing reconciles, so the artefact parks as `needs-attention` naming which
limit was hit and what makes room — a larger `ctx`, or a smaller `max_pixels` so the image costs
fewer tokens. **No claims are proposed from an artefact any page of which was cut off**, including
from the pages that fit: filing those would put a partial list of results in the record wearing the
same frontmatter as a complete one. The raw output of every page is still recorded verbatim. And a
truncated read is the one extraction that does not settle its idempotency key — the model never
finished answering, it proposed nothing that could be duplicated, and `ctx` and the image budget are
both outside the key, so `--artifact` genuinely re-reads it after either is changed.

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

`faster-whisper` (CTranslate2), `small` with `compute_type=int8` on the constrained tier.

**The weights are fetched by the same download as the reader**, pinned by sha256 in the same manifest,
and loaded from that local directory. Loading `small` by name makes `faster-whisper` fetch it from the
Hugging Face hub on first use — an unannounced network call with no pin — so a name is only ever
loaded from a cache already on disk, never fetched. `faster-whisper` is a default dependency, not an
extra: "install and it works" has to include voice notes.

**Decoding is PyAV first, `ffmpeg` on `PATH` as the fallback.** PyAV arrives with `faster-whisper` and
is ffmpeg's own libraries, so it reads the same containers a browser or phone produces — the reason
`ffmpeg` was chosen in the first place still holds, and nothing needs installing. Roughly
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

**The bundled reader's pins live in code, not in `config.toml`.** `agent/runtime/manifest.py` names each
file's URL at a fixed revision, its exact size and its sha256, and a pin a person can edit to anything
would defeat the verification it exists for. The pins are recorded in every `extraction.completed`
(`runtime.files`), which is what makes "which bytes read this" answerable a year later. Changing a pin
is a code change and a new release, and — because the alias carries the weights hash — a new model
identity, so documents read under the old one are distinguishable from those read under the new.

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

**Every expected medication, dose and allergy is scored correct, abstained or wrong.**

| Outcome | Meaning |
|---|---|
| correct | The claim matches, on the normalised key and the salt table. |
| abstained | The reader said it could not read that field or that page. |
| wrong | It asserted something false — a wrong dose, a wrong drug, a value under the wrong field or a leaked identifier — **or said nothing at all**. An unexpected claim about a medication or allergy that the fixture does not list in `also_true` is wrong too. A harness error is wrong. |

**The bar: wrong is 0%, and correct + abstained is 100%.** How the split falls between correct and
abstained is a quality measure, not a safety one. The asymmetry is visibility: a silent miss makes no
review item and nobody can notice it, which is the failure this project exists to prevent; an
abstention becomes a high-consequence "could not be read" item in the review queue, and the person
photographs it again or types it in. That is a working system, so the harness credits it — and never
credits an error, which the system puts in front of nobody.

**Run the corpus against both readers.** The bundled reader is the default, so it meets the bar in its
own right. If it asserts a false dose even once, it does not ship as the default. The bar is never
quietly lowered, and the result goes to a person. When it ships, its measured correct/abstained split
is stated where the person chooses it.

Five tests belong here rather than in the fixture corpus:

- `test_endpoint_guard_rejects_public_host` — a public hostname or a commercial API base URL fails at
  startup, and fails again on a host that re-resolves publicly mid-session.
- `test_capture_survives_offline_endpoint` — with the endpoint refused at the socket level, capture
  succeeds, the artefact lands in `raw/`, the job persists, and it drains on reconnect.
- `test_retry_is_idempotent` — a job retried three times across a restart produces exactly one
  `claim.proposed` per claim.
- `test_truncated_output_is_not_reported_as_malformed` — a `finish_reason: "length"` answer says the
  model ran out of room and says nothing about grammar settings. Beside it: the ceiling is raised and
  the page asked again, an ordinary page still costs one call, and a page that overruns the top of
  the ladder proposes nothing and parks naming the limit it hit.
- `test_grammar_probe_catches_an_unconstrained_server` — prose, JSON of the wrong shape, and an
  answer that never stops each fail the startup probe as a grammar problem. Beside it: a box that
  goes to sleep mid-probe is still reported as unreachable.

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
