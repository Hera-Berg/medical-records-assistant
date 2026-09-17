"""What the screens are told about the reader on this computer.

One function builds it, so the settings screen, the download screen and the
sidebar cannot describe one machine three ways. Every sentence comes from
:mod:`agent.runtime.states`; the numbers beside them are this machine's own —
bytes from the manifest, memory and disk from the operating system, seconds
measured from this device's own readings.

**Speed is measured, not promised.** Before this machine has read anything the
screen gives the honest range for its kind of hardware. After, it says what the
last few documents actually took *here* — from ``runtime.elapsed_s`` on this
device's own extraction events, never another machine's, because a laptop being
told how fast the desktop was is being told nothing.
"""

from __future__ import annotations

import statistics
import threading
from typing import Any, Sequence

from ..events.envelope import Event
from ..extract import propose
from ..runtime import choice as choice_mod
from ..runtime import download as download_mod
from ..runtime import manifest, measurements, platforms, states, supervisor
from ..runtime.store import Store

#: How many recent readings the measured speed is the median of.
SPEED_SAMPLE = 5

#: Said above the download button, before anything is fetched.
DOWNLOAD_EXPLANATION = (
    "Reading documents on this computer needs a model and the program that runs "
    "it. They are downloaded once, checked against fixed fingerprints before "
    "they are used, and kept outside your record's folder so they are never "
    "synced. Nothing from your record is sent — the only thing asked for is the "
    "files."
)

_LOCK = threading.Lock()


def downloader(state) -> download_mod.Downloader:
    """The process's one downloader, for the files this machine's choice needs."""
    with _LOCK:
        existing = getattr(state, "downloader", None)
        if existing is not None:
            return existing
        store = Store(vault_root=state.vault.root)

        def bundles() -> tuple[manifest.Bundle, ...]:
            chosen = choice_mod.load(state.vault)
            return manifest.required(platforms.current(), chosen.reads_here, chosen.model)

        state.downloader = download_mod.Downloader(store, bundles)  # type: ignore[attr-defined]
        return state.downloader  # type: ignore[attr-defined]


def _own_readings(events: Sequence[Event], device: str | None, alias: str | None) -> list[tuple[str, float]]:
    readings: list[tuple[str, float]] = []
    for event in events:
        if event.type != propose.EXTRACTION_COMPLETED or event.device != device:
            continue
        runtime = event.payload.get("runtime")
        if not isinstance(runtime, dict) or runtime.get("kind") != "bundled":
            continue
        if alias is not None and event.payload.get("model_reported") != alias:
            continue
        elapsed = runtime.get("elapsed_s")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed > 0:
            readings.append((event.ts, float(elapsed)))
    return readings


def speed(
    events: Sequence[Event],
    device: str | None,
    platform: str | None,
    waiting: int,
    alias: str | None = None,
) -> dict[str, Any]:
    """What to expect, from this device's own recent readings with this model where there are any."""
    readings = _own_readings(events, device, alias)
    recent = [seconds for _, seconds in sorted(readings)[-SPEED_SAMPLE:]]
    measured = round(statistics.median(recent)) if recent else None

    if measured is None:
        known = measurements.for_model(alias).speed.get(platform or "") if alias else None
        if known is not None:
            pace = (
                f"Reading takes about {_seconds(known.seconds_per_document)} a document "
                f"— measured on {known.machine}; this computer may differ."
            )
        else:
            pace = f"Reading on this computer takes {states.speed_estimate(platform)}."
    else:
        pace = f"Usually about {_seconds(measured)} a document on this computer."
    eta = None
    if measured is not None and waiting > 1:
        eta = f"Roughly {_duration(measured * waiting)} for all {waiting}."
    sentence = pace
    if waiting:
        noun = "document" if waiting == 1 else "documents"
        sentence += f" {waiting} {noun} waiting"
        if measured is not None and waiting > 1:
            sentence += f" — roughly {_duration(measured * waiting)} in all"
        sentence += "."
    return {
        "estimate": states.speed_estimate(platform),
        "measured_seconds": measured,
        "measured_from": len(recent),
        "waiting": waiting,
        # Without the count, for a place that has already said how many.
        "pace": pace,
        "eta": eta,
        "sentence": sentence,
    }


def _seconds(value: int) -> str:
    if value < 60:
        rounded = max(5, 5 * round(value / 5))
        return f"{rounded} seconds"
    rounded = 10 * round(value / 10)
    minutes, seconds = divmod(rounded, 60)
    if seconds == 0:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    return f"{minutes} minute{'s' if minutes != 1 else ''} {seconds} seconds"


def _duration(total: float) -> str:
    minutes = max(1, round(total / 60))
    return "a minute" if minutes == 1 else f"{minutes} minutes"


def payload(state) -> dict[str, Any]:
    """Everything the reader screens show, with nothing secret and nothing composed elsewhere."""
    vault = state.vault
    platform = platforms.current()
    current = choice_mod.load(vault)
    fetcher = downloader(state)
    progress = fetcher.progress()
    bundles = manifest.required(platform, current.reads_here, current.model)
    chosen_model = manifest.vision_model(current.model)
    memory = platforms.total_memory_bytes()
    snapshot = state.snapshot()
    waiting = state.queue().depth()
    device = None
    try:
        device = vault.identity.id
    except Exception:  # noqa: BLE001 - no identity means no readings of its own
        device = None

    reader_status: dict[str, Any] | None = None
    if current.reads_here:
        reader_status = supervisor.get(vault).status().to_dict()

    reason = progress.reason
    return {
        "choice": current.to_dict(),
        "options": [
            {"value": value, "label": choice_mod.LABELS[value], "current": value == current.reads_on}
            for value in choice_mod.CHOICES
        ],
        "demo": vault.is_demo,
        "platform": {
            "key": platform,
            "label": platforms.LABELS.get(platform or "", "This kind of computer"),
            "supported": bool(manifest.reader_bundles(platform)),
            # Said on screen. A build pinned for a platform nobody has run it on
            # is not the same claim as one that has been.
            "verified": platform in platforms.VERIFIED,
        },
        "memory": {
            "total_bytes": memory,
            "low": memory is not None and memory < platforms.LOW_MEMORY_BYTES,
        },
        "disk": {"free_bytes": platforms.free_disk_bytes(fetcher.store.root)},
        "files": {
            "explanation": DOWNLOAD_EXPLANATION,
            "location": str(fetcher.store.root),
            "hosts": list(manifest.hosts_contacted(bundles)),
            "bundles": [
                {"id": b.id, "title": b.title, "licence": b.licence, "size_bytes": b.size}
                for b in bundles
            ],
            "total_bytes": progress.total_bytes,
            "done_bytes": progress.done_bytes,
            # What pressing the button would actually fetch. Not the total: a
            # file placed by hand, or half a download from yesterday, is not
            # something a person should be asked to agree to again.
            "remaining_bytes": max(0, progress.total_bytes - progress.done_bytes),
            "state": progress.state,
            "current": progress.current,
            "reason": reason,
            "message": states.DOWNLOAD_MESSAGES.get(reason) if reason else None,
        },
        "reader": reader_status,
        "speed": (
            speed(snapshot.events, device, platform, waiting, alias=chosen_model.alias)
            if current.reads_here else None
        ),
        "models": [
            model_entry(model, current.model, fetcher.store, memory, platform, snapshot.events, device)
            for model in manifest.VISION_MODELS
        ],
    }


def _gb(value: int) -> str:
    """Memory only, as it is sold and reported: "8 GB" is 8 × 1024³ bytes.

    Download and disk sizes are sent as bytes and shown in decimal GB by the
    screen; this is never used for them.
    """
    gigabytes = value / 1024**3
    return f"{gigabytes:.0f} GB" if gigabytes == int(gigabytes) else f"{gigabytes:.1f} GB"


def model_entry(
    model: manifest.VisionModel,
    chosen: str,
    store: Store,
    memory: int | None,
    platform: str | None,
    events: Sequence[Event],
    device: str | None,
) -> dict[str, Any]:
    """One entry in the model list, with every number shown at the point of choosing.

    Nothing here is behind a link. The reason offering a model that failed the
    eval bar is acceptable is that the person chooses it knowing what it did, so
    what it did is in the entry itself: its size, the memory it needs, its speed
    here or "not measured", correct / left unread / wrong on the sample
    documents, and any failure a person found that nothing in the app can catch.
    """
    found = measurements.for_model(model.alias)
    own = _own_readings(events, device, model.alias)
    speed_here = found.speed.get(platform or "")

    if own:
        recent = [seconds for _, seconds in sorted(own)[-SPEED_SAMPLE:]]
        speed_sentence = f"About {_seconds(round(statistics.median(recent)))} a document on this computer."
    elif speed_here is not None:
        speed_sentence = (
            f"About {_seconds(speed_here.seconds_per_document)} a document, measured on "
            f"{speed_here.machine}."
        )
    else:
        label = platforms.LABELS.get(platform or "", "this kind of computer")
        speed_sentence = f"Speed not measured on {label}."

    accuracy = found.accuracy
    if accuracy is not None:
        accuracy_sentence = (
            f"On the {accuracy.fixtures} sample documents it was tested on, for medicines, "
            f"doses and allergies: {accuracy.correct} read correctly, {accuracy.abstained} "
            f"left for you to check, {accuracy.wrong} wrong."
        )
    else:
        accuracy_sentence = "Accuracy not measured."

    memory_warning = None
    if memory is not None and memory < model.ram_needed_bytes:
        memory_warning = (
            f"This computer has {_gb(memory)} of memory and this model needs about "
            f"{_gb(model.ram_needed_bytes)}. It will still run, but slowly, and other "
            f"programs will be squeezed while it reads. The system may stop it part-way "
            f"through a document — you would see that it stopped, and could try again."
        )

    downloaded = store.is_ready((model.bundle,))
    return {
        "id": model.id,
        "title": model.title,
        "recommended": model.recommended,
        "current": model.id == chosen,
        "size_bytes": model.bundle.size,
        "ram_needed_bytes": model.ram_needed_bytes,
        "downloaded": downloaded,
        "licence": model.licence,
        "speed": {
            "sentence": speed_sentence,
            "measured": bool(own) or speed_here is not None,
        },
        "accuracy": {
            "sentence": accuracy_sentence,
            **(accuracy.to_dict() if accuracy is not None else {}),
        },
        "known_failures": list(found.known_failures),
        "memory_warning": memory_warning,
    }
