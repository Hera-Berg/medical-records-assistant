"""Seeding a scratch vault with a realistic, entirely fictional record.

Builds a vault a person can open in a file browser and read by hand: real
documents in ``raw/``, a real event log, and a wiki whose every citation
resolves to a path that is actually there.

The documents are genuinely readable — see :mod:`agent.demo.documents`. They
were placeholder headers followed by padding for as long as there was no model
to read them, and the cost of leaving them that way was a demo vault against
which nothing downstream of ingest could be exercised at all.

Three guardrails, because a folder of invented medications is a genuinely
dangerous thing to mistake for a real one:

- It refuses to seed anywhere that is not empty, so it can never be pointed at a
  real vault and merged into it.
- It writes ``DEMO-DATA.md`` at the vault root, in the first place anyone opening
  the folder will look.
- It appends under the device id ``demo-device``, so the shard is named
  ``YYYY-MM.demo-device.jsonl`` and the log says what it is too. That identity is
  held in memory and never written to ``~/.config/health-agent/device``: seeding a
  demo must not mint or overwrite the identity of the machine it runs on.

A fourth guardrail runs the other way. A demo vault reaches no inference
endpoint at all unless ``--endpoint-from`` is given one, and when it is, both
``config.toml`` and ``DEMO-DATA.md`` name the file it came from — so a folder of
invented data can never quietly end up talking to a real box.

``ingested_ts`` is genuinely now, because the bytes genuinely enter the vault
now. Only the dates *on* the fictional documents are backdated, which is the
distinction the four-timestamp table exists to hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import ingest as ingest_mod
from .. import projection as projection_mod
from .. import vault as vault_mod
from ..config import CONFIG_FILENAME
from ..device import DeviceIdentity, current_hostname, current_platform
from ..errors import HealthAgentError
from ..events.envelope import format_ts
from ..vault import DEMO_MARKER_FILENAME, Vault
from . import documents, endpoint as endpoint_mod, stream
from .stream import ARTIFACTS

#: Written at the vault root, where a person opening the folder will see it.
#: The name lives in :mod:`agent.vault` so that code which only needs to ask
#: "is this a demo vault" — the inference layer, when it has to explain why
#: there is no endpoint — does not have to import the seeder to find out.
MARKER_FILENAME = DEMO_MARKER_FILENAME

#: The head of the demo's own ``config.toml``. Fixed, and never copied from
#: anywhere: ``sync_profile`` in particular is always ``local``, because a
#: scratch folder is not on anybody's Dropbox and a demo vault claiming to be
#: would have the scan reporting sync forks that cannot exist.
_CONFIG_HEAD = """\
# Demo vault configuration. Every record in this folder is invented — see
# DEMO-DATA.md.

sync_profile = "local"
port = 7777
locale = "en"
"""

#: What sits where ``[models.vlm]`` would, when there is none.
#:
#: The demo used to carry the template's placeholder MagicDNS name, which made
#: ``extract`` against a demo vault fail with a DNS error indistinguishable from
#: the user's own box being asleep — a wrong answer to "is my endpoint working",
#: produced by a folder of invented data. A demo seeds a record, not a
#: connection to a model, so by default the table is absent and the absence is
#: explained where someone hits it.
_NO_ENDPOINT = """\
# There is deliberately no [models.vlm] table here. A demo vault seeds a record,
# not an inference endpoint: pointing this at a placeholder host would make
# `health-agent extract` fail with a DNS error that looks exactly like your own
# box being asleep. Run extraction against your real vault, paste your
# [models.vlm] table in below, or re-seed with `demo --endpoint-from <config>`
# to copy one — see MODELS.md.
"""

#: ``[models.asr]`` is always written: speech runs locally, so it needs no
#: endpoint and no credential, and phase 6 can be exercised against a demo vault
#: exactly as seeded.
_CONFIG_TAIL = """\
[models.asr]
name         = "faster-whisper-small"
compute_type = "int8"
"""


def demo_config(endpoint: endpoint_mod.CopiedEndpoint | None = None) -> str:
    """The demo's ``config.toml``, with or without a copied endpoint.

    When one was copied the file says so in a comment naming the source. The
    terminal output that announced the copy scrolls away and the vault does not,
    so a folder of invented data must never be able to acquire a real endpoint
    that only a past run ever mentioned.
    """
    parts = [_CONFIG_HEAD]
    if endpoint is None:
        parts.append(_NO_ENDPOINT)
    else:
        parts.append(
            f"# [models.vlm] below was copied by `demo --endpoint-from` from\n"
            f"#     {endpoint.source}\n"
            f"# Only that table and [models.vlm.auth] were copied — no credential, no\n"
            f"# sync_profile, nothing else. This is a demo vault holding invented\n"
            f"# data, and it now talks to a real inference box: delete the table if\n"
            f"# that is not what you meant.\n"
            f"\n{endpoint.toml}"
        )
    parts.append(_CONFIG_TAIL)
    return "\n".join(parts)


#: Its own device id, so the shard filename says so as well.
DEMO_DEVICE = "demo-device"

MARKER_TEXT = """\
# This is demo data

Every medication, allergy, problem, practitioner and document in this folder is
**invented**. No part of it describes a real person. It was written by
`health-agent demo` to give the renderer something realistic to be read against,
and to give the extraction path something it can actually be run over.

The artefacts under `raw/` are real files. The prescriptions and the specialist
letter are rendered images with legible text on them, the discharge summary and
the pathology report are PDFs with genuine text layers, and the voice note is a
decodable 16 kHz mono WAV. What each one says matches the claims the demo seeded
against it, so `health-agent extract` over this folder can be compared against a
hand-authored answer.

One honest gap: the voice note is a tone rather than speech — nothing here
synthesises a voice — so transcribing it will correctly produce nothing.

{endpoint}

Delete this folder whenever you like. Nothing outside it refers to it.
"""

_NO_ENDPOINT_NOTE = """\
This vault carries no `[models.vlm]` endpoint: a demo seeds a record, not a
connection to a model. Point `--vault` at your real vault to extract, add an
endpoint to `config.toml` here, or re-seed with
`health-agent demo <path> --endpoint-from <config>` to copy one across."""

_COPIED_ENDPOINT_NOTE = """\
**This vault has a real inference endpoint.** `[models.vlm]` in `config.toml`
was copied by `--endpoint-from` from

    {source}

Only that table and `[models.vlm.auth]` came across — no credential, no
`sync_profile`, nothing else. So a folder of invented data will talk to a real
box when you run `health-agent extract` against it. Delete the table if that is
not what you meant."""


def marker_text(endpoint: endpoint_mod.CopiedEndpoint | None = None) -> str:
    """``DEMO-DATA.md``, saying whether this vault can reach a model."""
    note = (
        _NO_ENDPOINT_NOTE
        if endpoint is None
        else _COPIED_ENDPOINT_NOTE.format(source=endpoint.source)
    )
    return MARKER_TEXT.format(endpoint=note)


#: Where each kind of demo artefact is pretending to have come from.
_SOURCES = {"photo": "camera", "scan": "import", "pdf": "import", "audio": "recorder"}


@dataclass(frozen=True)
class DemoReport:
    """What the seeding produced, for the CLI to print."""

    root: Path
    artifacts: int
    events: int
    rebuild: projection_mod.RebuildReport
    #: The endpoint copied in, if one was. ``None`` is the default and the
    #: ordinary case — a demo vault reaches no model.
    endpoint: endpoint_mod.CopiedEndpoint | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "artifacts": self.artifacts,
            "events": self.events,
            "endpoint": self.endpoint.to_dict() if self.endpoint else None,
            "rebuild": self.rebuild.to_dict(),
        }


def _identity() -> DeviceIdentity:
    """An in-memory identity for the demo shard.

    Hostname and platform are this machine's, so ``require_appendable`` is
    satisfied honestly rather than bypassed — the demo really is appending from
    the machine it is running on.
    """
    return DeviceIdentity(
        id=DEMO_DEVICE,
        label="demo data",
        created=format_ts(datetime.now(timezone.utc)),
        hostname=current_hostname(),
        platform=current_platform(),
        # "env" rather than "file", because there is no file: this identity is
        # built for the run and thrown away. The machine-match check is satisfied
        # honestly either way — the hostname above is this machine's.
        source="env",
    )


def _require_empty(root: Path) -> None:
    if not root.exists():
        return
    if not root.is_dir():
        raise HealthAgentError(
            f"{root} exists and is not a directory; refusing to seed demo data over it"
        )
    existing = sorted(child.name for child in root.iterdir())
    if existing:
        raise HealthAgentError(
            f"{root} is not empty ({', '.join(existing[:5])}"
            f"{', …' if len(existing) > 5 else ''}). `demo` only ever seeds an empty "
            f"folder, so that it can never be pointed at a real vault and merged "
            f"into it. Choose a new path, or delete this one yourself."
        )


def seed(
    root: str | Path,
    as_of: datetime | None = None,
    endpoint_from: str | Path | None = None,
) -> DemoReport:
    """Create a demo vault at *root* and rebuild it.

    *as_of* anchors the scenario: every document date is an offset back from it,
    and it is what the rebuild measures staleness from. Defaults to now, which is
    what makes the demo look the same on any day it is run.

    *endpoint_from* copies ``[models.vlm]`` and ``[models.vlm.auth]`` out of the
    config file it names, and nothing else — see :mod:`agent.demo.endpoint`.
    Absent, which is the default, the vault reaches no model at all.
    """
    root = Path(root).expanduser()
    anchor = as_of or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    # Both refusals come before any write. A folder half-seeded because the
    # endpoint source turned out to hold a credential is a folder someone then
    # has to reason about.
    endpoint = endpoint_mod.copy_from(endpoint_from) if endpoint_from else None
    _require_empty(root)

    vault_mod.scaffold(root)
    (root / CONFIG_FILENAME).write_text(demo_config(endpoint), encoding="utf-8")
    (root / MARKER_FILENAME).write_text(marker_text(endpoint), encoding="utf-8")

    vault = Vault.open(root, identity=_identity())

    clock = stream.Clock(anchor)
    shorts: dict[str, str] = {}
    ingested: dict[str, str] = {}
    captured: dict[str, str] = {}
    for artifact in ARTIFACTS:
        document = documents.render(artifact.name, anchor.date())
        # captured_ts is set because for a demo we genuinely know it — these
        # stand in for a camera capture. ingested_ts is left to the real ingest,
        # which records now, because now is when the bytes arrived.
        context = ingest_mod.CaptureContext(
            source=_SOURCES[artifact.kind],
            original_filename=f"{artifact.name}.{document.extension}",
            # None throughout today: every demo artefact's bytes already name
            # their own type. The field is here because a WebM would not — its
            # header cannot say whether there is a video track — and phase 6
            # records WebM from `MediaRecorder`.
            declared_mime=document.declared_mime,
            captured_ts=clock.ts(artifact.captured, hour=10),
            note=artifact.note,
        )
        result = ingest_mod.ingest_bytes(vault, document.data, context)
        shorts[artifact.name] = result.event.payload["short"]
        ingested[artifact.name] = result.event.payload["ingested_ts"]
        captured[artifact.name] = result.event.payload["captured_ts"]

    events = stream.build(DEMO_DEVICE, anchor, shorts, ingested, captured)
    for event in events:
        vault.append(event)

    rebuild = projection_mod.rebuild(vault, as_of=anchor)
    return DemoReport(
        root=root,
        artifacts=len(ARTIFACTS),
        events=len(events),
        rebuild=rebuild,
        endpoint=endpoint,
    )
