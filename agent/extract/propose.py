"""Turning what the model said into events, or into a reason it did not.

Two event types come out of here and they are deliberately separate.
``extraction.completed`` holds the model's **raw answer, verbatim**, with the
model identity, the sampling parameters, the prompt hashes and what preprocessing
did. ``claim.proposed`` holds one parsed claim each. ``CLAUDE.md``: "When the
model is swapped for a better one you need to re-derive everything and diff it,
and you cannot do that from parsed claims."

**Nothing here decides anything about the record.** A proposal is a proposal: the
consequence gate, the reconciliation rules and the user's taps all happen later,
in the projection, from the log. This module cannot promote a claim, cannot
resolve a conflict, and cannot write to ``wiki/``.

**Emission is keyed and conditional.** The key is
``(artifact_hash, prompt_hash, model_id)`` and it is checked against the log
before a single event is appended, so a job retried three times across a restart
produces exactly one set of claims. That is the whole of
``test_retry_is_idempotent``, and it lives here rather than in the queue because
the log is the only thing that survives everything.

**The four timestamps are copied, never invented.** ``ingested_ts`` and
``captured_ts`` come from the artefact's own ingest event and are whatever that
event recorded, including ``null``. ``artifact_ts`` is set only when the model
read a day-precise date off the document — a month-precise document date is not
a timestamp, and writing one would fake precision the page never had.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..events import envelope
from ..events.envelope import Event
from ..llm.client import Completion
from ..projection import citations as citations_mod
from ..projection.dates import PRECISIONS
from .crossverify import Verification
from .prompts import Prompt
from .validate import Extraction, ReadClaim

EXTRACTION_COMPLETED = "extraction.completed"
CLAIM_PROPOSED = "claim.proposed"
MODEL_OBSERVED = "model.identity.observed"


@dataclass(frozen=True)
class Key:
    """What makes one extraction the same piece of work as another.

    Not the job id and not the event id: those are per-attempt and would let a
    retry after a crash emit a second set of claims. This is a fact about *what
    was asked of which model about which bytes*, so two attempts at the same work
    collide by construction.
    """

    artifact: str
    prompt_hash: str
    model: str

    def as_dict(self) -> dict[str, str]:
        return {
            "artifact": self.artifact,
            "prompt_hash": self.prompt_hash,
            "model": self.model,
        }

    @property
    def tuple(self) -> tuple[str, str, str]:
        return (self.artifact, self.prompt_hash, self.model)


def completed_keys(events: Iterable[Event]) -> set[tuple[str, str, str]]:
    """Every extraction already recorded in the log, as keys.

    Read from ``extraction.completed`` rather than from ``claim.proposed``,
    because an extraction that legitimately produced no claims — a page that
    states nothing, a page nobody could read — is still done, and re-running it
    every time the queue drained would be an infinite retry of a settled answer.
    """
    keys: set[tuple[str, str, str]] = set()
    for event in events:
        if event.type != EXTRACTION_COMPLETED:
            continue
        recorded = event.payload.get("key")
        if isinstance(recorded, dict):
            artifact = recorded.get("artifact")
            prompt_hash = recorded.get("prompt_hash")
            model = recorded.get("model")
            if all(isinstance(part, str) for part in (artifact, prompt_hash, model)):
                keys.add((artifact, prompt_hash, model))
    return keys


def observed_models(events: Iterable[Event]) -> set[str]:
    """Identity strings the log has already recorded seeing."""
    seen: set[str] = set()
    for event in events:
        if event.type != MODEL_OBSERVED:
            continue
        name = event.payload.get("model")
        if isinstance(name, str) and name:
            seen.add(name)
    return seen


def _provenance(key: Key, model_rev: str | None = None) -> dict[str, Any]:
    """What every agent-authored event carries.

    ``model_rev`` is the identity string the server reported. A remote model has
    no content hash to pin, so the string the box gave is the closest thing to
    one, and it is what makes "which model produced this claim" answerable a
    year later.
    """
    return {
        "model": key.model,
        "model_rev": model_rev or key.model,
        "prompt_hash": key.prompt_hash,
        "artifact": key.artifact,
    }


def _document_timestamp(document_date: Mapping[str, Any] | None) -> str | None:
    """``artifact_ts`` from the model's document date, or ``None``.

    Only a day-precise date becomes a timestamp. "June 2026" is a real thing to
    know about a document and it is kept on the extraction event, but it is not
    a moment, and writing ``2026-06-15T00:00:00Z`` would put a precision in the
    record that the page never had.
    """
    if not isinstance(document_date, dict):
        return None
    if document_date.get("precision") != PRECISIONS[0]:
        return None
    if document_date.get("uncertainty_days"):
        return None
    value = document_date.get("value")
    if not isinstance(value, str) or len(value) != 10:
        return None
    return f"{value}T00:00:00Z"


def extraction_event(
    device: str,
    key: Key,
    completion: Completion,
    extraction: Extraction,
    prompts_used: Sequence[Prompt],
    image_notes: Sequence[Mapping[str, Any]] = (),
    deterministic: Mapping[str, Any] | None = None,
    ts: str | None = None,
) -> Event:
    """The model's answer, stored verbatim, with everything needed to re-derive it."""
    return envelope.new(
        EXTRACTION_COMPLETED,
        device,
        ts=ts,
        provenance=_provenance(key, completion.model),
        payload={
            "key": key.as_dict(),
            "artifact": key.artifact,
            # Verbatim. Not the parsed form, not a tidied form: a model swap is
            # diffed against this, and a parse is a lossy view of it.
            "raw_output": completion.content,
            "model_reported": completion.model,
            "sampling": dict(completion.sampling),
            "stripped_reasoning": completion.stripped_reasoning,
            "latency_s": round(completion.latency_s, 3),
            "usage": completion.usage,
            "prompt_version": prompts_used[0].version if prompts_used else None,
            "prompt_hashes": sorted({p.prompt_hash for p in prompts_used}),
            "images": [dict(note) for note in image_notes],
            "deterministic_read": dict(deterministic) if deterministic else None,
            "artifact_kind": extraction.artifact_kind,
            "readable": extraction.readable,
            "unreadable_reason": extraction.unreadable_reason,
            "document_date": extraction.document_date,
            "claims_proposed": len(extraction.claims),
            "rejected": extraction.describe_rejections(),
            "notes": list(extraction.notes),
        },
    )


def claim_event(
    device: str,
    key: Key,
    claim: ReadClaim,
    artifact: citations_mod.Artifact | None,
    document_date: Mapping[str, Any] | None,
    verification: Verification | None = None,
    model_rev: str | None = None,
    ts: str | None = None,
) -> Event:
    """One ``claim.proposed``, with all four timestamps set honestly.

    ``consequence`` is written for provenance only. The projection recomputes it
    from the predicate and reports a disagreement as an anomaly — the payload's
    value has never gated anything, and this event carries it so that a
    disagreement is visible rather than merely impossible.
    """
    notes = list(claim.notes)
    payload: dict[str, Any] = {
        "subject": claim.subject,
        "predicate": claim.predicate,
        "value": claim.value_literal,
        "evidence_tier": claim.evidence_tier,
        "consequence": claim.consequence,
        "confidence": claim.confidence,
        "source_span": claim.source_span,
        "occurred_at": claim.occurred_at,
        # The phrase the page used, whenever it was not a calendar date. Kept
        # so the projection can raise a dateable review item: discarding it
        # would lose information the record exists to hold.
        "occurred_span": claim.occurred_span,
        "artifact_ts": _document_timestamp(document_date),
        "captured_ts": artifact.captured_ts if artifact else None,
        "ingested_ts": artifact.ingested_ts if artifact else None,
    }
    if claim.dispense:
        payload["dispense"] = dict(claim.dispense)
    if verification is not None:
        payload["cross_check"] = verification.describe()
        if verification.is_contradicted:
            # Both readings, and nothing picking between them. Two independent
            # readers disagreeing about a dose is the signal, not a problem to
            # resolve here.
            payload["readings"] = [
                {"reader": "vision-language model", "value": claim.value_literal},
                {"reader": "deterministic", "value": verification.rival},
            ]
        if verification.reason:
            notes.append(verification.reason)
    if notes:
        payload["notes"] = notes
    return envelope.new(
        CLAIM_PROPOSED,
        device,
        ts=ts,
        provenance=_provenance(key, model_rev),
        payload=payload,
    )


def model_event(device: str, key: Key, reported: str, ts: str | None = None) -> Event:
    """First sight of a model identity string. The registry MODELS.md asks for.

    Appended once per identity. "Last seen" needs no event of its own: it is the
    most recent ``extraction.completed`` carrying that string.
    """
    return envelope.new(
        MODEL_OBSERVED,
        device,
        ts=ts,
        provenance=_provenance(key, reported),
        payload={"model": reported, "configured": key.model},
    )


@dataclass(frozen=True)
class Proposal:
    """Everything one artefact's extraction wants to append, and why."""

    key: Key
    events: tuple[Event, ...] = ()
    skipped: bool = False
    reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def claims(self) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.type == CLAIM_PROPOSED)

    def describe(self) -> str:
        if self.skipped:
            return f"already extracted: {self.reason}"
        return f"{len(self.claims)} claims proposed from artefact {self.key.artifact}"


def build(
    device: str,
    key: Key,
    completion: Completion,
    extraction: Extraction,
    prompts_used: Sequence[Prompt],
    artifact: citations_mod.Artifact | None,
    already: set[tuple[str, str, str]] | None = None,
    verifications: Mapping[int, Verification] | None = None,
    image_notes: Sequence[Mapping[str, Any]] = (),
    deterministic: Mapping[str, Any] | None = None,
    seen_models: set[str] | None = None,
    ts: str | None = None,
) -> Proposal:
    """Assemble the events for one artefact, or decline because it is already done.

    The idempotency check comes first and is absolute. Nothing is appended for a
    key the log already carries, however many times the job is retried and
    whether or not the process died between the extraction event and the claims.
    """
    if already is not None and key.tuple in already:
        return Proposal(
            key=key,
            skipped=True,
            reason=(
                f"artefact {key.artifact} has already been read by {key.model} under "
                f"this prompt; re-reading it would propose the same claims a second "
                f"time"
            ),
        )

    events: list[Event] = [
        extraction_event(
            device,
            key,
            completion,
            extraction,
            prompts_used,
            image_notes=image_notes,
            deterministic=deterministic,
            ts=ts,
        )
    ]
    if seen_models is not None and completion.model not in seen_models:
        events.append(model_event(device, key, completion.model, ts=ts))

    verifications = verifications or {}
    for index, claim in enumerate(extraction.claims):
        events.append(
            claim_event(
                device,
                key,
                claim,
                artifact,
                extraction.document_date,
                verification=verifications.get(index),
                model_rev=completion.model,
                ts=ts,
            )
        )

    notes = list(extraction.notes)
    if not extraction.readable:
        notes.append(
            f"artefact {key.artifact} could not be read — review manually: "
            f"{extraction.unreadable_reason}"
        )
    contradicted = [
        index for index, check in verifications.items() if check.is_contradicted
    ]
    if contradicted:
        notes.append(
            f"{len(contradicted)} claim(s) were read differently by the two readers and "
            f"carry both readings for review"
        )
    return Proposal(key=key, events=tuple(events), notes=tuple(notes))


def raw_output_of(event: Event) -> Any:
    """The model's answer as recorded, parsed if it is JSON.

    Used by re-extraction to diff one model's reading against another's without
    re-running either.
    """
    raw = event.payload.get("raw_output")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def summarise(proposals: Iterable[Proposal]) -> dict[str, int]:
    proposals = list(proposals)
    return {
        "artifacts": len(proposals),
        "skipped": sum(1 for p in proposals if p.skipped),
        "claims": sum(len(p.claims) for p in proposals),
        "events": sum(len(p.events) for p in proposals),
    }
