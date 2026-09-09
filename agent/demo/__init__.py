"""Seeding a scratch vault with a realistic, entirely fictional record.

Nothing produces claim events until phase 4, and the only ones that exist live
inside the test suite, where they are asserted on rather than read. This builds a
vault a person can open in a file browser and read by hand: real files in
``raw/``, a real event log, and a wiki whose every citation resolves to a path
that is actually there.

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
from ..config import CONFIG_FILENAME, CONFIG_TEMPLATE
from ..device import DeviceIdentity, current_hostname, current_platform
from ..errors import HealthAgentError
from ..events.envelope import format_ts
from ..vault import Vault
from . import stream
from .stream import ARTIFACTS

#: Written at the vault root, where a person opening the folder will see it.
MARKER_FILENAME = "DEMO-DATA.md"

#: Its own device id, so the shard filename says so as well.
DEMO_DEVICE = "demo-device"

MARKER_TEXT = """\
# This is demo data

Every medication, allergy, problem, practitioner and document in this folder is
**invented**. No part of it describes a real person. It was written by
`health-agent demo` to give the renderer something realistic to be read against
before there was a model to produce anything.

The artefacts under `raw/` are placeholder files with the right magic bytes and
the wrong contents — they are there so that citations resolve to a real path, not
because they contain anything.

Delete this folder whenever you like. Nothing outside it refers to it.
"""

#: Placeholder bytes with genuine format headers, so mime sniffing behaves as it
#: does on real files. The payload is padding: a citation needs a path to
#: resolve to, not a readable document.
_HEADERS = {
    "jpeg": (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00", "jpg"),
    "pdf": (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n", "pdf"),
    "webm": (b"\x1aE\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x82\x84webm", "webm"),
}


#: Where each kind of demo artefact is pretending to have come from.
_SOURCES = {"jpeg": "camera", "pdf": "import", "webm": "recorder"}


def _bytes_for(artifact: stream.Artifact) -> bytes:
    """Deterministic placeholder bytes, distinct per artefact.

    Distinct matters: identical bytes would deduplicate on hash and the second
    one would become an ``artifact.reseen`` event citing the first, which is
    correct behaviour and the wrong thing to demonstrate here.
    """
    header, _ = _HEADERS[artifact.kind]
    filler = f"\n% demo artefact: {artifact.name} — {artifact.note}\n".encode("utf-8")
    return header + filler + b"\x00" * 512


def _extension(artifact: stream.Artifact) -> str:
    return _HEADERS[artifact.kind][1]


@dataclass(frozen=True)
class DemoReport:
    """What the seeding produced, for the CLI to print."""

    root: Path
    artifacts: int
    events: int
    rebuild: projection_mod.RebuildReport

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "artifacts": self.artifacts,
            "events": self.events,
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


def seed(root: str | Path, as_of: datetime | None = None) -> DemoReport:
    """Create a demo vault at *root* and rebuild it.

    *as_of* anchors the scenario: every document date is an offset back from it,
    and it is what the rebuild measures staleness from. Defaults to now, which is
    what makes the demo look the same on any day it is run.
    """
    root = Path(root).expanduser()
    anchor = as_of or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    _require_empty(root)
    vault_mod.scaffold(root)
    (root / CONFIG_FILENAME).write_text(CONFIG_TEMPLATE, encoding="utf-8")
    (root / MARKER_FILENAME).write_text(MARKER_TEXT, encoding="utf-8")

    vault = Vault.open(root, identity=_identity())

    shorts: dict[str, str] = {}
    ingested: dict[str, str] = {}
    captured: dict[str, str] = {}
    for artifact in ARTIFACTS:
        # captured_ts is set because for a demo we genuinely know it — these
        # stand in for a camera capture. ingested_ts is left to the real ingest,
        # which records now, because now is when the bytes arrived.
        context = ingest_mod.CaptureContext(
            source=_SOURCES[artifact.kind],
            original_filename=f"{artifact.name}.{_extension(artifact)}",
            # A webm container sniffs as video/webm, because the header cannot
            # say the file has no video track. MediaRecorder's voice notes are
            # audio/webm and phase 6 will declare it the same way.
            declared_mime="audio/webm" if artifact.kind == "webm" else None,
            captured_ts=stream._Clock(anchor).ts(artifact.captured, hour=10),
            note=artifact.note,
        )
        result = ingest_mod.ingest_bytes(vault, _bytes_for(artifact), context)
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
    )
