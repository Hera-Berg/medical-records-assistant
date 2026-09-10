"""Scoring the speech model against the recordings in the golden corpus.

Separate from :mod:`agent.extract.evaluate`, and the split is not tidiness.
**The speech model's contract is the transcript, not the claims.** Reading
``40mg daily`` out of "up to forty milligrams daily" is the vision model's job,
so scoring the ASR path on claim recall would be scoring it on work it does not
do — and would make it un-runnable without the box, which is the one thing
speech is specifically built not to need.

So this scores two things and nothing else:

**Term recall.** Did the drug names, the practitioner name and the temporal
phrase survive? A mangled drug name is not a rough transcript, it is an
unmatched entity: "search-reline" resolves to no medication, so it becomes a
second page for a drug that already has one.

**Silence.** Did a recording with no speech in it produce no text at all? A
single hallucinated segment here is a subtitle artefact in a medical record.

And it reports **biased against unbiased**, which is the one number that says
whether the hotword list is earning its place. ``MODELS.md`` claims biasing is
"what makes the transcripts usable" on this tier; this is where that claim is
either demonstrated or disproved on the day someone doubts it.

There is no combined score and no F1, for the same reason as the other harness:
a single number can improve while the thing that matters gets worse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import transcribe as transcribe_mod

#: Matching is on the normalised word, not the literal. "Sertraline," with a
#: comma and a capital is the term; so is "40mg" where the fixture says "40 mg".
_WORD = re.compile(r"[^\w]+", re.UNICODE)


def _normalise(text: str) -> str:
    return " " + " ".join(_WORD.sub(" ", text).lower().split()) + " "


def found(term: str, text: str) -> bool:
    """Whether *term* survived into *text*, compared as words rather than bytes."""
    return _normalise(term).strip() in _normalise(text)


@dataclass(frozen=True)
class FixtureScore:
    """One recording, read one way."""

    name: str
    biased: bool
    expected: tuple[str, ...] = ()
    heard: tuple[str, ...] = ()
    missed: tuple[str, ...] = ()
    text: str = ""
    dropped: int = 0
    expects_silence: bool = False
    unavailable: str | None = None

    @property
    def ok(self) -> bool:
        if self.unavailable is not None:
            return False
        if self.expects_silence:
            # Nothing at all. Not "almost nothing", not "one short segment".
            return not self.text.strip()
        return not self.missed

    @property
    def recall(self) -> float | None:
        if not self.expected:
            return None
        return len(self.heard) / len(self.expected)

    def describe(self) -> str:
        how = "biased" if self.biased else "unbiased"
        if self.unavailable is not None:
            return f"{self.name:<22} {how:<8} not run — {self.unavailable}"
        if self.expects_silence:
            verdict = "silent" if self.ok else f"HEARD {self.text.strip()[:60]!r}"
            return f"{self.name:<22} {how:<8} {verdict}"
        recall = self.recall or 0.0
        detail = f"{len(self.heard)}/{len(self.expected)} terms ({recall:.0%})"
        if self.missed:
            detail += f" — missed {', '.join(self.missed)}"
        return f"{self.name:<22} {how:<8} {detail}"


@dataclass(frozen=True)
class SpeechReport:
    """The whole run: every recording, read both ways."""

    scores: tuple[FixtureScore, ...] = ()
    skipped: tuple[str, ...] = ()
    model: str = transcribe_mod.DEFAULT_MODEL
    compute_type: str = transcribe_mod.DEFAULT_COMPUTE_TYPE
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def biased(self) -> tuple[FixtureScore, ...]:
        return tuple(score for score in self.scores if score.biased)

    @property
    def ok(self) -> bool:
        """The gate reads the biased runs only — that is how the app runs."""
        return bool(self.biased) and all(score.ok for score in self.biased)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "compute_type": self.compute_type,
            "ok": self.ok,
            "skipped": list(self.skipped),
            "notes": list(self.notes),
            "scores": [
                {
                    "name": score.name,
                    "biased": score.biased,
                    "expected": list(score.expected),
                    "heard": list(score.heard),
                    "missed": list(score.missed),
                    "recall": score.recall,
                    "dropped": score.dropped,
                    "ok": score.ok,
                    "text": score.text,
                    "unavailable": score.unavailable,
                }
                for score in self.scores
            ],
        }

    def describe(self) -> Sequence[str]:
        lines = [f"speech    {self.model} ({self.compute_type})", ""]
        lines.extend(score.describe() for score in self.scores)
        gained = self._gained()
        if gained is not None:
            lines.append("")
            lines.append(
                f"biasing   recovered {gained} term(s) the unbiased read got wrong"
                if gained
                else "biasing   recovered nothing on this corpus — worth "
                "investigating before trusting it on a real record"
            )
        for note in self.notes:
            lines.append(f"note      {note}")
        if self.skipped:
            lines.append(f"not run   {', '.join(self.skipped)}")
        lines.append("")
        lines.append("PASS" if self.ok else "FAIL")
        return lines

    def _gained(self) -> int | None:
        """How many terms biasing recovered. ``None`` if it was not run both ways."""
        unbiased = {score.name: score for score in self.scores if not score.biased}
        if not unbiased:
            return None
        gained = 0
        for score in self.biased:
            other = unbiased.get(score.name)
            if other is None or other.unavailable is not None:
                continue
            gained += len(set(score.heard) - set(other.heard))
        return gained


def score_one(
    name: str,
    audio: Path,
    expects: Sequence[str],
    expects_silence: bool,
    hotwords: Sequence[str],
    biased: bool,
    language: str = "en",
    model: str = transcribe_mod.DEFAULT_MODEL,
    compute_type: str = transcribe_mod.DEFAULT_COMPUTE_TYPE,
    speech: transcribe_mod.Speech | None = None,
) -> FixtureScore:
    """Read one recording and say how much of it survived."""
    transcript = transcribe_mod.transcribe(
        audio,
        language=language,
        hotwords=tuple(hotwords) if biased else (),
        model=model,
        compute_type=compute_type,
        speech=speech,
    )
    if not transcript.is_available:
        return FixtureScore(
            name=name,
            biased=biased,
            expected=tuple(expects),
            expects_silence=expects_silence,
            unavailable=transcript.unavailable,
        )
    heard = tuple(term for term in expects if found(term, transcript.text))
    return FixtureScore(
        name=name,
        biased=biased,
        expected=tuple(expects),
        heard=heard,
        missed=tuple(term for term in expects if term not in heard),
        text=transcript.text,
        dropped=len(transcript.dropped),
        expects_silence=expects_silence,
    )


def run_corpus(
    fixtures: Iterable[Any],
    hotwords: Sequence[str],
    workdir: Path,
    language: str = "en",
    model: str = transcribe_mod.DEFAULT_MODEL,
    compute_type: str = transcribe_mod.DEFAULT_COMPUTE_TYPE,
    both_ways: bool = True,
    speech: transcribe_mod.Speech | None = None,
) -> SpeechReport:
    """Score every recording in *fixtures*, biased and (by default) unbiased.

    *workdir* is where the rendered audio is written. Never inside a vault: a
    fixture is not an artefact and must not end up looking like one.
    """
    scores: list[FixtureScore] = []
    skipped: list[str] = []
    notes: list[str] = []

    for fixture in fixtures:
        if not str(fixture.mime).startswith(("audio/", "video/")):
            continue
        missing = fixture.missing_tools
        if missing:
            skipped.append(f"{fixture.name} (needs {', '.join(missing)})")
            continue
        audio = workdir / f"{fixture.name}.webm"
        audio.write_bytes(fixture.bytes())
        expects = tuple(fixture.transcript_expects)
        silent = not expects and not fixture.readable
        for biased in (True, False) if both_ways else (True,):
            scores.append(
                score_one(
                    name=fixture.name,
                    audio=audio,
                    expects=expects,
                    expects_silence=silent,
                    hotwords=hotwords,
                    biased=biased,
                    language=language,
                    model=model,
                    compute_type=compute_type,
                    speech=speech,
                )
            )

    if not scores:
        notes.append(
            "no recordings were scored. The corpus declares two; both need "
            "espeak-ng and ffmpeg to render"
        )
    return SpeechReport(
        scores=tuple(scores),
        skipped=tuple(skipped),
        model=model,
        compute_type=compute_type,
        notes=tuple(notes),
    )
