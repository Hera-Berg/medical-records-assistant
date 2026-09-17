"""Scoring a reader against the golden corpus: correct, abstained, or wrong.

Every expected claim on medications, doses and allergies ends in exactly one of
three outcomes, and the gate reads two numbers:

- **wrong must be zero.** Wrong is an asserted falsehood — a wrong dose, a wrong
  drug, a value filed under the wrong field — and it is also *silence*: a
  medication that produced neither a claim nor an abstention. A silent miss makes
  no review item and nobody can notice it, which is the failure ``CLAUDE.md``
  calls worse than no record.
- **correct + abstained must be everything.** An abstention — the reader saying
  it could not read that field, or that page — becomes a visible "could not be
  read" in the review queue, and the person photographs it again or types it
  in. That is a working system. How the split falls between correct and
  abstained is a measure of quality, not of safety.

Scoring abstention and error identically, as recall did, could not tell a
cautious reader from a broken one. An error in the harness is wrong, not
abstained: the system does not put a harness exception in front of anybody.

**A claim the fixture does not expect is wrong too**, when it is about a
medication or an allergy and the fixture does not list it as also true. A
reading of "mefenamic acid 500mg" off a page that says metformin is not a false
positive for a person to catch — it is the wrong drug in a medical record.

**Matching is on the normalised key, never the literal**, through the same
:mod:`~agent.projection.values` comparison and salt table the projection uses.
There is no combined score.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..projection import drugs, subjects, values
from .validate import Abstention, ReadClaim, merge


def _key(subject: str, predicate: str, value: str) -> tuple[str, str, str]:
    """What makes two readings the same reading.

    The projection's own :class:`~agent.projection.values.Value` key, which is
    what decides elsewhere whether two sources agree or a slot is conflicted. It
    folds "5 mg daily" and "5mg daily" together, and "twice daily" and "bd", so
    a model that read the page correctly and spelled it differently passes.

    Using anything weaker here — bare text normalisation, say — would fail
    correct readings and push the next prompt change in exactly the wrong
    direction, which for a corpus whose entire job is gating a model swap would
    be worse than having no corpus.

    The subject is folded through the salt table for the same reason. A label
    reading ``PERINDOPRIL ARGININE`` mints ``med:perindopril-arginine``, which
    the projection files under ``med:perindopril``; a model that read the page
    exactly right would otherwise score a miss against a fixture written with
    the plain name, and on a corpus where medication recall must be 100% that
    miss would block a model swap for being correct.
    """
    normalised = subject.strip().lower()
    parsed_subject = subjects.parse(normalised)
    normalised = drugs.alias_for(parsed_subject) or normalised
    parsed = values.parse(value)
    key = parsed.key if parsed is not None else values.normalise_text(value)
    return (normalised, predicate.strip().lower(), key)


CORRECT = "correct"
ABSTAINED = "abstained"
WRONG = "wrong"

#: Subject kinds whose claims are medications, doses and allergies — the gate.
CRITICAL_KINDS = ("med", "allergy")

#: Which group of the reader a subject kind is asked about in.
_FAMILY_OF_KIND = {"med": "medications", "allergy": "allergies", "problem": "problems", "person": "problems"}

#: Predicates that only say a thing is named on the page. True whenever the thing
#: itself is one the fixture knows is there.
_NAMING = ("name", "substance")


def _subject_key(subject: str) -> str:
    normalised = subject.strip().lower()
    parsed = subjects.parse(normalised)
    return drugs.alias_for(parsed) or normalised


def _key(subject: str, predicate: str, value: str) -> tuple[str, str, str]:
    """What makes two readings the same reading.

    The projection's own :class:`~agent.projection.values.Value` key, folded
    through the salt table: a label reading ``PERINDOPRIL ARGININE`` is filed
    under ``med:perindopril``, and a reader that got it right must not score
    wrong for it.
    """
    parsed = values.parse(value)
    key = parsed.key if parsed is not None else values.normalise_text(value)
    return (_subject_key(subject), predicate.strip().lower(), key)


@dataclass(frozen=True)
class Scored:
    """One expected claim, or one claim nobody expected, and what became of it."""

    key: tuple[str, str, str]
    outcome: str
    critical: bool
    detail: str = ""

    def describe(self) -> str:
        subject, predicate, value = self.key
        line = f"{self.outcome:<9} {subject} {predicate} {value}"
        return f"{line} — {self.detail}" if self.detail else line


@dataclass(frozen=True)
class FixtureResult:
    """How one artefact scored."""

    name: str
    #: One entry per expected claim.
    expected: tuple[Scored, ...] = ()
    #: Claims nobody expected. Wrong when critical; reported either way.
    unexpected: tuple[Scored, ...] = ()
    readable: bool = True
    expected_readable: bool = True
    error: str | None = None
    #: Wall time reading this fixture took, and whether it was a document (an
    #: image or a PDF) rather than a recording — speed is quoted per document.
    elapsed_s: float | None = None
    is_document: bool = False

    def _count(self, outcome: str, critical: bool = True) -> int:
        return sum(
            1 for item in (*self.expected, *self.unexpected)
            if item.outcome == outcome and (item.critical or not critical)
        )

    @property
    def correct(self) -> int:
        return self._count(CORRECT)

    @property
    def abstained(self) -> int:
        return self._count(ABSTAINED)

    @property
    def wrong(self) -> int:
        return self._count(WRONG)

    @property
    def critical_expected(self) -> int:
        return sum(1 for item in self.expected if item.critical)

    @property
    def ok(self) -> bool:
        """Nothing critical wrong, and every critical claim correct or abstained."""
        if self.error is not None or self.wrong:
            return False
        return self.correct + self.abstained >= self.critical_expected

    def describe(self) -> list[str]:
        flag = "ok  " if self.ok else "FAIL"
        head = (
            f"{flag}   {self.name}: correct {self.correct}, abstained "
            f"{self.abstained}, wrong {self.wrong}"
        )
        lines = [head]
        if self.error:
            lines.append(f"         error: {self.error}")
        for item in (*self.expected, *self.unexpected):
            if item.outcome != CORRECT or not item.critical:
                lines.append(f"         {item.describe()}")
        return lines


@dataclass(frozen=True)
class Report:
    """The whole corpus, scored. Three counts on the gated claims, never combined."""

    results: tuple[FixtureResult, ...] = ()
    model: str = ""
    tier: str = "endpoint"
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Generation speeds the reader's own server reported, one per answer.
    generation_rates: tuple[float, ...] = ()

    def seconds_per_document(self) -> int | None:
        """The median wall time of the documents that were read, to the second."""
        times = sorted(
            r.elapsed_s for r in self.results
            if r.is_document and r.elapsed_s is not None and r.error is None
        )
        return round(statistics.median(times)) if times else None

    def generation_tokens_per_second(self) -> float | None:
        return round(statistics.median(self.generation_rates), 1) if self.generation_rates else None

    @property
    def correct(self) -> int:
        return sum(r.correct for r in self.results)

    @property
    def abstained(self) -> int:
        return sum(r.abstained for r in self.results)

    @property
    def wrong(self) -> int:
        return sum(r.wrong for r in self.results)

    @property
    def total(self) -> int:
        """Every gated outcome: expected claims plus unexpected critical assertions."""
        return self.correct + self.abstained + self.wrong

    def share(self, count: int) -> Fraction | None:
        """Exact arithmetic, so an unchanged reader reports an unchanged number."""
        return Fraction(count, self.total) if self.total else None

    @property
    def ok(self) -> bool:
        """The gate: wrong is zero, and correct plus abstained is everything."""
        return all(result.ok for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "tier": self.tier,
            "ok": self.ok,
            "timing": {
                "seconds_per_document": self.seconds_per_document(),
                "generation_tokens_per_second": self.generation_tokens_per_second(),
            },
            "critical": {
                "correct": self.correct,
                "abstained": self.abstained,
                "wrong": self.wrong,
                "correct_share": _percent(self.share(self.correct)),
                "abstained_share": _percent(self.share(self.abstained)),
                "wrong_share": _percent(self.share(self.wrong)),
            },
            "fixtures": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "correct": r.correct,
                    "abstained": r.abstained,
                    "wrong": r.wrong,
                    "expected": [
                        {"claim": list(s.key), "outcome": s.outcome, "critical": s.critical, "detail": s.detail}
                        for s in r.expected
                    ],
                    "unexpected": [
                        {"claim": list(s.key), "outcome": s.outcome, "critical": s.critical, "detail": s.detail}
                        for s in r.unexpected
                    ],
                    "error": r.error,
                    "elapsed_s": r.elapsed_s,
                }
                for r in self.results
            ],
            "notes": list(self.notes),
        }

    def describe(self) -> list[str]:
        lines: list[str] = []
        for result in self.results:
            lines.extend(result.describe())
        lines.append("")
        lines.append("medications, doses and allergies:")
        lines.append(f"  correct     {self.correct}  ({_percent(self.share(self.correct))})")
        lines.append(f"  abstained   {self.abstained}  ({_percent(self.share(self.abstained))})")
        lines.append(f"  wrong       {self.wrong}  ({_percent(self.share(self.wrong))})")
        if not self.ok:
            lines.append("")
            lines.append(
                "FAILED. Wrong must be 0 on medications, doses and allergies, and "
                "every one of them must be read correctly or honestly left unread. "
                "An asserted falsehood or a silent miss reaches nobody. Do not accept "
                "this model or prompt change."
            )
        return lines


def _percent(value: Fraction | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def score(
    fixture,
    claims: Sequence[ReadClaim],
    readable: bool = True,
    error: str | None = None,
    abstentions: Sequence[Abstention] = (),
) -> FixtureResult:
    """Score one fixture: every expected claim correct, abstained or wrong."""
    expected_keys = [
        (_key(e.subject, e.predicate, e.value), e.critical) for e in fixture.expected
    ]
    also_true = {_key(*item) for item in getattr(fixture, "also_true", ())}
    known_subjects = {key[0] for key, _ in expected_keys} | {key[0] for key in also_true}

    if error is not None:
        return FixtureResult(
            fixture.name,
            expected=tuple(
                Scored(key, WRONG, critical, f"the read errored: {error}")
                for key, critical in expected_keys
            ),
            readable=False,
            expected_readable=fixture.readable,
            error=error,
        )

    produced = [(_key(c.subject, c.predicate, c.value_literal), c) for c in claims]
    produced_keys = {key for key, _ in produced}
    by_slot: dict[tuple[str, str], list[str]] = {}
    for key, _claim in produced:
        by_slot.setdefault(key[:2], []).append(key[2])

    named = [a for a in abstentions if a.subject]
    nameless: dict[str, int] = {}
    for item in abstentions:
        if not item.subject:
            nameless[item.family] = nameless.get(item.family, 0) + 1
    covered = {_subject_key(a.subject) for a in named}

    scored: list[Scored] = []
    wrong_slots: set[tuple[str, str]] = set()
    for key, critical in expected_keys:
        slot = key[:2]
        family = _FAMILY_OF_KIND.get(slot[0].split(":", 1)[0], "")
        if key in produced_keys:
            scored.append(Scored(key, CORRECT, critical))
        elif slot in by_slot:
            wrong_slots.add(slot)
            scored.append(
                Scored(key, WRONG, critical, f"asserted {', '.join(sorted(by_slot[slot]))} instead")
            )
        elif not readable:
            scored.append(Scored(key, ABSTAINED, critical, "the reader said it could not read the page"))
        elif slot[0] in covered:
            reasons = sorted({f"{a.field}: {a.reason}" for a in named if _subject_key(a.subject) == slot[0]})
            scored.append(Scored(key, ABSTAINED, critical, "; ".join(reasons)))
        elif nameless.get(family):
            nameless[family] -= 1
            scored.append(Scored(key, ABSTAINED, critical, f"an unnamed entry in {family} could not be read"))
        else:
            scored.append(Scored(key, WRONG, critical, "silently missing — no claim and no abstention"))

    expected_set = {key for key, _ in expected_keys}
    unexpected: list[Scored] = []
    for key, _claim in produced:
        if key in expected_set:
            continue
        subject, predicate, _value = key
        critical = subject.split(":", 1)[0] in CRITICAL_KINDS
        if not fixture.readable:
            unexpected.append(Scored(key, WRONG, True, "a claim from a page nobody can read is a fabrication"))
        elif key in also_true:
            unexpected.append(Scored(key, CORRECT, False, "not required, and true"))
        elif predicate in _NAMING and subject in known_subjects:
            unexpected.append(Scored(key, CORRECT, False, "names something that is on the page"))
        elif (subject, predicate) in wrong_slots:
            continue  # already counted against the expected claim it displaced
        elif critical:
            unexpected.append(Scored(key, WRONG, True, "asserted, and not what the page says"))
        else:
            unexpected.append(Scored(key, "unexpected", False, "not in the fixture; not gated"))

    return FixtureResult(
        name=fixture.name,
        expected=tuple(scored),
        unexpected=tuple(unexpected),
        readable=readable,
        expected_readable=fixture.readable,
    )


def report(results: Iterable[FixtureResult], model: str, tier: str = "endpoint", notes=()) -> Report:
    return Report(results=tuple(results), model=model, tier=tier, notes=tuple(notes))


def compare_tiers(remote: Report, fallback: Report) -> list[str]:
    """Fixtures one reader gets wrong that another does not.

    Kept for comparing two readers on the same corpus. A fixture the second
    reader gets wrong more often is one to route to a person on that reader —
    never a worse result to accept because the other one was unreachable.
    """
    worse: list[str] = []
    by_name = {result.name: result for result in remote.results}
    for result in fallback.results:
        better = by_name.get(result.name)
        if better is None:
            continue
        if result.wrong > better.wrong or (not result.ok and better.ok):
            worse.append(
                f"{result.name}: the {fallback.tier} reader gets {result.wrong} gated "
                f"claim(s) wrong where the {remote.tier} reader gets {better.wrong}. "
                f"Route this artefact to a person on the {fallback.tier} reader — "
                f"never accept the worse reading because the other was unreachable"
            )
    return worse


# --- running the corpus ----------------------------------------------------


def run_corpus(
    client,
    fixtures,
    locale: str = "en",
    hotwords: Sequence[str] = (),
    workdir: Path | None = None,
) -> Report:
    """Read every fixture exactly as extraction reads it, and score the result.

    Through :func:`agent.extract.reader.read` — the same conversation, the same
    ceiling ladder, the same checks — so the harness scores what extraction does
    rather than a second implementation of it.
    """
    from . import images as images_mod  # noqa: PLC0415 - avoids an import cycle
    from . import prompts as prompts_mod
    from . import reader as reader_mod
    from ..errors import HealthAgentError

    results: list[FixtureResult] = []
    rates: list[float] = []
    model = client.settings.model
    for fixture in fixtures:
        started = time.monotonic()
        before = len(results)
        try:
            data = fixture.bytes()
            if fixture.mime.startswith("audio/"):
                results.append(
                    _score_recording(
                        client, fixture, data, locale=locale, hotwords=hotwords,
                        workdir=workdir,
                    )
                )
                continue
            if fixture.mime == "application/pdf":
                document = images_mod.prepare_pdf(data, client.settings.long_edge)
            else:
                document = images_mod.Document(
                    pages=(images_mod.prepare(data, client.settings.long_edge, page=1),)
                )
            if not document.is_readable:
                results.append(score(fixture, [], error=document.unreadable))
                continue

            answers = []
            for page in document.pages:
                prompt = prompts_mod.build(page, total_pages=len(document.pages))
                read = reader_mod.read(client, prompt, mime=fixture.mime, locale=locale)
                rates.extend(_rates(read.turns))
                answers.append(read.extraction)
            extraction = merge(answers)
            results.append(_score_extraction(fixture, extraction))
        except HealthAgentError as exc:
            results.append(score(fixture, [], error=str(exc)))
        if len(results) > before:
            results[-1] = replace(
                results[-1],
                elapsed_s=round(time.monotonic() - started, 1),
                is_document=not fixture.mime.startswith("audio/"),
            )
    runtime = (getattr(client, "runtime", None) or {}).get("kind", "endpoint")
    return Report(
        results=tuple(results), model=model, tier=runtime, generation_rates=tuple(rates)
    )


def _rates(turns) -> list[float]:
    found = []
    for turn in turns:
        timings = turn.completion.raw.get("timings") if isinstance(turn.completion.raw, dict) else None
        rate = timings.get("predicted_per_second") if isinstance(timings, dict) else None
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
            found.append(float(rate))
    return found


def _score_extraction(fixture, extraction) -> FixtureResult:
    """An answer the system refused or could not finish is not an abstention.

    A cut-off or schema-refused answer parks the artefact for a person, which is
    visible — but it is the harness's job to say the reader failed to answer, so
    it is scored as an error rather than credited as caution.
    """
    if extraction.refused:
        return score(fixture, [], error=extraction.unreadable_reason or "the answer was refused")
    return score(
        fixture,
        extraction.claims,
        readable=extraction.readable,
        abstentions=extraction.abstentions,
    )


def _score_recording(
    client,
    fixture,
    data: bytes,
    locale: str,
    hotwords: Sequence[str],
    workdir: Path | None,
) -> FixtureResult:
    """Type a recording up locally, then read the words exactly as extraction does."""
    import tempfile  # noqa: PLC0415 - only needed here

    from ..asr import transcribe as transcribe_mod  # noqa: PLC0415
    from . import reader as reader_mod  # noqa: PLC0415
    from . import transcripts as transcripts_mod  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="health-agent-eval-audio-") as scratch:
        audio = Path(workdir or scratch) / f"{fixture.name}.webm"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(data)
        transcript = transcribe_mod.transcribe(audio, language=locale, hotwords=tuple(hotwords))

    if not transcript.is_available:
        return score(fixture, [], error=transcript.unavailable)
    if not transcript.text.strip():
        # Silence and a cough: nothing asked of the reader and nothing claimed,
        # which for that fixture is the expected result.
        return score(fixture, [], readable=False)

    prompt = transcripts_mod.build(transcript.text)
    extraction = reader_mod.read(client, prompt, mime=fixture.mime, locale=locale).extraction
    held, _notes = transcripts_mod.force_tier(extraction.claims)
    from dataclasses import replace as _replace  # noqa: PLC0415

    return _score_extraction(fixture, _replace(extraction, claims=tuple(held)))
