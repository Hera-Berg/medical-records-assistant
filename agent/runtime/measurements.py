"""What each model was measured to do, for the person choosing between them.

A committed data file, written by ``health-agent eval --record`` from a real run
on a real machine and read by the settings screen. Keyed by the model's identity
string, which carries its weights hash: a measurement belongs to exact bytes,
and a re-pinned model starts with none rather than inheriting numbers from
different weights.

Three kinds of fact, and each says where it came from:

- **accuracy** on medications, doses and allergies from the eval corpus —
  correct, abstained and wrong, with the prompt version it was measured under.
- **speed** per platform, with the machine it was measured on. A number from
  one laptop is not a promise about another, so the machine is always shown
  beside it; where nothing was measured the screen says "not measured".
- **known failures**: findings a person wrote down from reading a run, in the
  words the settings screen shows. Kept by ``--record``, never overwritten by it.
"""

from __future__ import annotations
from .. import files

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("measurements.json")


@dataclass(frozen=True)
class Accuracy:
    correct: int
    abstained: int
    wrong: int
    fixtures: int
    prompt_version: int
    measured: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct, "abstained": self.abstained, "wrong": self.wrong,
            "fixtures": self.fixtures, "prompt_version": self.prompt_version,
            "measured": self.measured,
        }


@dataclass(frozen=True)
class Speed:
    seconds_per_document: int
    generation_tokens_per_second: float | None
    machine: str
    measured: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seconds_per_document": self.seconds_per_document,
            "generation_tokens_per_second": self.generation_tokens_per_second,
            "machine": self.machine, "measured": self.measured,
        }


@dataclass(frozen=True)
class Measured:
    accuracy: Accuracy | None = None
    speed: dict[str, Speed] = field(default_factory=dict)
    known_failures: tuple[str, ...] = ()


def _read(path: Path | None = None) -> dict[str, Any]:
    path = path or PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return models if isinstance(models, dict) else {}


def for_model(alias: str, path: Path | None = None) -> Measured:
    raw = _read(path).get(alias)
    if not isinstance(raw, dict):
        return Measured()
    accuracy = raw.get("accuracy")
    speeds = raw.get("speed") if isinstance(raw.get("speed"), dict) else {}
    return Measured(
        accuracy=Accuracy(**accuracy) if isinstance(accuracy, dict) else None,
        speed={
            platform: Speed(**item) for platform, item in speeds.items() if isinstance(item, dict)
        },
        known_failures=tuple(
            str(item) for item in raw.get("known_failures", ()) if isinstance(item, str)
        ),
    )


def record(
    alias: str,
    accuracy: Accuracy,
    platform: str | None,
    speed: Speed | None,
    path: Path | None = None,
) -> None:
    """Write one run's numbers for *alias*, keeping any known failures already written."""
    path = path or PATH
    models = _read(path)
    entry = dict(models.get(alias) or {})
    entry["accuracy"] = accuracy.to_dict()
    if platform and speed is not None:
        speeds = dict(entry.get("speed") or {})
        speeds[platform] = speed.to_dict()
        entry["speed"] = speeds
    entry.setdefault("known_failures", [])
    models[alias] = entry
    body = json.dumps({"models": models}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    files.write_text(path, body, 0o644)
