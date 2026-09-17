"""Scoring a model against the golden corpus.

``MODELS.md``: "**Recall on medications, doses and allergies must be 100%.** A
false positive gets caught in review; a missed medication is invisible and is
precisely the failure this project exists to prevent. Report recall and precision
separately and never trade recall for precision."

Two rules follow, and both are enforced here rather than left to whoever reads
the report.

**There is no combined score.** No F1, no weighted average, nothing that can go
up because precision improved while recall fell. The two numbers are reported
apart and the pass/fail gate reads recall on the critical categories only.

**Matching is on the normalised key, never the literal.** A model that writes
"5 mg daily" where the fixture says "5mg daily" has read the page correctly, and
an eval that failed it would push the next prompt change in exactly the wrong
direction. This is the same comparison the projection uses to decide whether two
readings agree — see ``values.normalise_text``.

The harness is deliberately not a test that runs on every commit: it needs the
box. It is a gate on a **model or prompt change**, which is when it is worth
minutes and a warm GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..projection import drugs, subjects, values
from .validate import ReadClaim


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


@dataclass(frozen=True)
class FixtureResult:
    """How one artefact scored."""

    name: str
    matched: tuple[tuple[str, str, str], ...] = ()
    missed: tuple[tuple[str, str, str], ...] = ()
    extra: tuple[tuple[str, str, str], ...] = ()
    #: The critical subsets, carried rather than re-derived. Critical recall is
    #: the number the gate reads, and computing it from `matched` minus
    #: `missed_critical` would count every non-critical match as critical and
    #: report a pass that had not been earned.
    matched_critical: tuple[tuple[str, str, str], ...] = ()
    missed_critical: tuple[tuple[str, str, str], ...] = ()
    readable: bool = True
    expected_readable: bool = True
    error: str | None = None

    @property
    def ok(self) -> bool:
        """A fixture passes only if nothing critical was missed.

        Extras do not fail it. A false positive is caught in review by a person
        looking at a diff; a missed medication is not caught by anything.
        """
        return not self.missed_critical and self.error is None

    def describe(self) -> str:
        if self.error:
            return f"ERROR  {self.name}: {self.error}"
        parts = [f"{len(self.matched)} found"]
        if self.missed:
            parts.append(f"{len(self.missed)} missed")
        if self.extra:
            parts.append(f"{len(self.extra)} extra")
        flag = "ok  " if self.ok else "FAIL"
        return f"{flag}   {self.name}: {', '.join(parts)}"


@dataclass(frozen=True)
class Report:
    """The whole corpus, scored. Two numbers, never combined into one."""

    results: tuple[FixtureResult, ...] = ()
    model: str = ""
    tier: str = "remote"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> int:
        return sum(len(r.matched) for r in self.results)

    @property
    def missed(self) -> int:
        return sum(len(r.missed) for r in self.results)

    @property
    def extra(self) -> int:
        return sum(len(r.extra) for r in self.results)

    def recall(self) -> Fraction | None:
        """Of everything the corpus says is there, how much was found.

        Exact arithmetic. A float here would make two runs of an unchanged model
        differ in the last digit and turn a stable number into a moving one.
        """
        total = self.matched + self.missed
        return Fraction(self.matched, total) if total else None

    def precision(self) -> Fraction | None:
        """Of everything found, how much the corpus says is really there."""
        total = self.matched + self.extra
        return Fraction(self.matched, total) if total else None

    def critical_recall(self) -> Fraction | None:
        """Recall on medications, doses and allergies. Must be 1."""
        found = sum(len(r.matched_critical) for r in self.results)
        missed = sum(len(r.missed_critical) for r in self.results)
        total = found + missed
        return Fraction(found, total) if total else None

    @property
    def ok(self) -> bool:
        """The gate. Critical recall of 100%, and nothing errored."""
        return all(result.ok for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "tier": self.tier,
            "ok": self.ok,
            # Separately. Always separately.
            "recall": _percent(self.recall()),
            "precision": _percent(self.precision()),
            "critical_recall": _percent(self.critical_recall()),
            "matched": self.matched,
            "missed": self.missed,
            "extra": self.extra,
            "fixtures": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "matched": [list(m) for m in r.matched],
                    "missed": [list(m) for m in r.missed],
                    "extra": [list(m) for m in r.extra],
                    "error": r.error,
                }
                for r in self.results
            ],
            "notes": list(self.notes),
        }

    def describe(self) -> list[str]:
        lines = [result.describe() for result in self.results]
        lines.append("")
        lines.append(f"recall            {_percent(self.recall())}")
        lines.append(f"precision         {_percent(self.precision())}")
        lines.append(f"critical recall   {_percent(self.critical_recall())}")
        if not self.ok:
            lines.append("")
            lines.append(
                "FAILED. Recall on medications, doses and allergies must be 100%: a "
                "false positive is caught in review, a missed medication is caught by "
                "nothing. Do not accept this model or prompt change."
            )
            for result in self.results:
                for item in result.missed_critical:
                    lines.append(f"  missed  {result.name}: {' '.join(item)}")
        return lines


def _percent(value: Fraction | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def score(fixture, claims: Sequence[ReadClaim], readable: bool = True, error: str | None = None) -> FixtureResult:
    """Compare one fixture's expected claims against what the model produced.

    An error is scored as a miss of everything the fixture expected. It used to
    leave the fixture's claims out of recall altogether, so a recording that
    errored raised the headline number by removing two critical claims from the
    denominator — a harness that improves when a read fails is not a gate.
    """
    if error is not None:
        expected_all = tuple(
            sorted(_key(e.subject, e.predicate, e.value) for e in fixture.expected)
        )
        return FixtureResult(
            fixture.name,
            missed=expected_all,
            missed_critical=tuple(
                sorted(_key(e.subject, e.predicate, e.value) for e in fixture.critical)
            ),
            error=error,
            expected_readable=fixture.readable,
        )

    expected = {_key(e.subject, e.predicate, e.value): e for e in fixture.expected}
    produced = {_key(c.subject, c.predicate, c.value_literal) for c in claims}

    matched = tuple(sorted(produced & set(expected)))
    missed = tuple(sorted(set(expected) - produced))
    extra = tuple(sorted(produced - set(expected)))
    matched_critical = tuple(item for item in matched if expected[item].critical)
    missed_critical = tuple(item for item in missed if expected[item].critical)

    if not fixture.readable and claims:
        # A page nobody can read that produced claims anyway is the worst
        # outcome in the corpus, and it is not a precision problem — it is a
        # fabrication. Recorded as critical so the gate fails.
        missed_critical = missed_critical + (("<unreadable>", "should-be", "nothing"),)

    return FixtureResult(
        name=fixture.name,
        matched=matched,
        missed=missed,
        extra=extra,
        matched_critical=matched_critical,
        missed_critical=missed_critical,
        readable=readable,
        expected_readable=fixture.readable,
    )


def report(results: Iterable[FixtureResult], model: str, tier: str = "remote", notes=()) -> Report:
    return Report(results=tuple(results), model=model, tier=tier, notes=tuple(notes))


def compare_tiers(remote: Report, fallback: Report) -> list[str]:
    """Fixtures the fallback tier reads worse than the remote one.

    ``MODELS.md``: "If 4B recall on medications, doses or allergies falls below
    9B on any fixture, that fixture becomes a required review case at the
    fallback tier — the answer is to route it to a human, never to quietly
    accept the worse result because the box was unreachable."
    """
    worse: list[str] = []
    by_name = {result.name: result for result in remote.results}
    for result in fallback.results:
        better = by_name.get(result.name)
        if better is None:
            continue
        if len(result.missed_critical) > len(better.missed_critical):
            worse.append(
                f"{result.name}: the fallback tier misses "
                f"{len(result.missed_critical)} critical claim(s) the remote model "
                f"finds. Route this artefact to a person when running on the "
                f"fallback — never accept the worse reading because the box was "
                f"unreachable"
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
    """Read every fixture with *client* and score the result.

    Needs the box, so this is never part of the ordinary test suite. It is a
    gate on a model or prompt change — the moment when minutes and a warm GPU
    are worth spending, and the moment a silent recall regression would
    otherwise be accepted.

    Recordings go through both readers, in the order a real capture does: the
    local speech model types them up, and the box reads the words. Their claims
    were deferred through phases 6 and 7 with nothing scoring them, which is the
    condition a golden corpus exists to prevent — two fixtures carrying
    hand-written expected claims that no run ever compared against.
    """
    from . import budget as budget_mod  # noqa: PLC0415 - avoids an import cycle
    from . import images as images_mod  # noqa: PLC0415 - avoids an import cycle
    from . import prompts as prompts_mod
    from . import transcripts as transcripts_mod
    from .schema import EXTRACTION_SCHEMA, SCHEMA_NAME
    from .validate import merge, read as read_answer
    from ..errors import OutputTruncated
    from ..llm.client import parse_completion
    from ..errors import HealthAgentError

    results: list[FixtureResult] = []
    model = client.settings.model
    for fixture in fixtures:
        try:
            data = fixture.bytes()
            if fixture.mime.startswith("audio/"):
                results.append(
                    _score_recording(
                        client,
                        fixture,
                        data,
                        locale=locale,
                        hotwords=hotwords,
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
                results.append(
                    score(fixture, [], readable=False, error=document.unreadable)
                )
                continue

            answers = []
            for page in document.pages:
                prompt = prompts_mod.build(page, total_pages=len(document.pages))
                # The same ladder real extraction climbs, so a page that needs a
                # second call with more room is scored on that second call.
                completion, _raised = budget_mod.ask(
                    client, prompt.to_list(), EXTRACTION_SCHEMA, SCHEMA_NAME
                )
                try:
                    payload = parse_completion(completion)
                except OutputTruncated:
                    from .validate import Extraction

                    # Scored as a miss, and named as the miss it is. A fixture
                    # the box ran out of room on is a fixture whose recall is
                    # zero, and calling that "not JSON" would send whoever reads
                    # the report to the server's grammar settings.
                    answers.append(
                        Extraction(
                            readable=False,
                            truncated=True,
                            unreadable_reason="cut off at the token ceiling",
                        )
                    )
                    continue
                except HealthAgentError:
                    from .validate import Extraction

                    answers.append(
                        Extraction(readable=False, unreadable_reason="not JSON")
                    )
                    continue
                answers.append(read_answer(payload, mime=fixture.mime, locale=locale))
            extraction = merge(answers)
            results.append(
                score(fixture, extraction.claims, readable=extraction.readable)
            )
        except HealthAgentError as exc:
            results.append(score(fixture, [], error=str(exc)))
    runtime = (getattr(client, "runtime", None) or {}).get("kind", "endpoint")
    return report(results, model=model, tier=runtime)


def _score_recording(
    client,
    fixture,
    data: bytes,
    locale: str,
    hotwords: Sequence[str],
    workdir: Path | None,
) -> FixtureResult:
    """Type a recording up locally, then read the words with the box.

    Both stages, deliberately. Scoring the transcript prompt against a
    hand-written transcript would test the prompt and nothing else; what has to
    hold is that a drug name survives Whisper *and* the reading that follows it,
    because a name mangled in the first stage is an unmatched entity in the
    second and there is nothing downstream to notice.
    """
    import tempfile  # noqa: PLC0415 - only needed here

    from ..asr import transcribe as transcribe_mod  # noqa: PLC0415
    from ..errors import HealthAgentError  # noqa: PLC0415
    from ..llm.client import parse_completion  # noqa: PLC0415
    from . import transcripts as transcripts_mod  # noqa: PLC0415
    from .validate import Extraction, read as read_answer  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="health-agent-eval-audio-") as scratch:
        audio = Path(workdir or scratch) / f"{fixture.name}.webm"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(data)
        transcript = transcribe_mod.transcribe(
            audio,
            language=locale,
            hotwords=tuple(hotwords),
        )

    if not transcript.is_available:
        return score(fixture, [], readable=False, error=transcript.unavailable)
    if not transcript.text.strip():
        # Silence and a cough. No speech, nothing asked of the box, no claims —
        # which for that fixture is the expected result rather than a failure.
        return score(fixture, [], readable=False, error=None)

    from . import budget as budget_mod  # noqa: PLC0415

    prompt = transcripts_mod.build(transcript.text)
    completion, _raised = budget_mod.ask(
        client,
        prompt.to_list(),
        transcripts_mod.TRANSCRIPT_SCHEMA,
        transcripts_mod.SCHEMA_NAME,
    )
    try:
        payload = parse_completion(completion)
    except HealthAgentError as exc:
        # The message says which of the two it was — an unfinished answer or an
        # invalid one — because a recall failure with the wrong cause attached
        # is what makes a model swap get accepted on a bad report.
        return score(fixture, [], readable=False, error=str(exc))

    extraction = read_answer(payload, mime=fixture.mime, locale=locale)
    held, _notes = transcripts_mod.force_tier(extraction.claims)
    return score(fixture, held, readable=extraction.readable)
