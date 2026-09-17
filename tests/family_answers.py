"""Answering the reader's three turns from one hand-written answer.

Tests written before a page was read in groups describe what a model says about
a page as a single answer: ``artifact_kind``, ``readable``, ``document_date`` and
a list of claims. The reader now asks three questions — medications, allergies,
problems — each with its own schema. Rather than rewrite every test's idea of
what the page says, this translates that one answer into the answer each turn
would have given, by reading which schema the request asked for.

Only the shapes the old answers used are translated. Anything else — a body that
is not JSON, a claim of a kind no group asks about — is passed through untouched,
so a test about malformed output still sends malformed output.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx



def family_of(request: httpx.Request) -> str | None:
    try:
        body = json.loads(request.content)
    except ValueError:
        return None
    name = (((body.get("response_format") or {}).get("json_schema") or {}).get("name")) or ""
    return name.removeprefix("health_record_") or None


def _common(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_tier": claim.get("evidence_tier", "prescriber-issued"),
        "source_span": claim.get("source_span", ""),
        "confidence": claim.get("confidence", 0.9),
    }


def _dated(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "occurred_at": claim.get("occurred_at"),
        "occurred_span": claim.get("occurred_span"),
    }


def _medication(claim: dict[str, Any]) -> dict[str, Any]:
    value = claim.get("value_literal", "")
    strength, frequency, stopped = None, None, None
    if claim.get("predicate") == "status":
        stopped = value
    elif claim.get("predicate") == "dose":
        from agent.projection import values  # noqa: PLC0415

        match = values._AMOUNT_RE.search(value) or values._WORD_AMOUNT_RE.search(value)
        if match:
            strength = value[: match.end()].strip()
            frequency = value[match.end():].strip() or None
        else:
            strength = value
    return {
        "name": claim.get("subject_name", ""),
        "strength": strength,
        "frequency": frequency,
        "stopped": stopped,
        "dispense": claim.get("dispense"),
        **_dated(claim),
        **_common(claim),
    }


def translate(old: dict[str, Any], family: str) -> dict[str, Any]:
    claims = old.get("claims") or []
    if family == "medications":
        return {
            "artifact_kind": old.get("artifact_kind", "other"),
            "readable": old.get("readable", True),
            "unreadable_reason": old.get("unreadable_reason"),
            "document_date": old.get("document_date"),
            "medications": [_medication(c) for c in claims if c.get("subject_kind") == "med"],
            "unclear": old.get("unclear_medications", []),
        }
    if family == "allergies":
        return {
            "allergies": [
                {
                    "substance": c.get("subject_name", ""),
                    "reaction": c.get("value_literal") if c.get("predicate") == "reaction" else None,
                    **_dated(c),
                    **_common(c),
                }
                for c in claims
                if c.get("subject_kind") == "allergy"
            ],
            "unclear": old.get("unclear_allergies", []),
        }
    return {
        "problems": [
            {"name": c.get("subject_name", ""), **_dated(c), **_common(c)}
            for c in claims
            if c.get("subject_kind") == "problem"
        ],
        "people": [
            {
                "name": c.get("subject_name", ""),
                "role": c.get("value_literal") if c.get("predicate") == "role" else None,
                **_common(c),
            }
            for c in claims
            if c.get("subject_kind") == "person"
        ],
        "unclear": [],
    }


def completion(request: httpx.Request, body: dict[str, Any]) -> dict[str, Any]:
    """*body* as it would have answered the turn *request* is asking."""
    family = family_of(request)
    try:
        content = body["choices"][0]["message"]["content"]
        old = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError):
        return body
    if family is None or not isinstance(old, dict) or "claims" not in old:
        return body
    answered = copy.deepcopy(body)
    answered["choices"][0]["message"]["content"] = json.dumps(translate(old, family))
    return answered


def response(request: httpx.Request, body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=completion(request, body))


def wrap(handler):
    """A mock transport handler whose answers are translated for the turn asked."""

    def translated(request: httpx.Request) -> httpx.Response:
        response = handler(request)
        if response.status_code != 200 or family_of(request) is None:
            return response
        try:
            body = response.json()
        except ValueError:
            return response
        if not isinstance(body, dict) or "choices" not in body:
            return response
        return httpx.Response(200, json=completion(request, body))

    return translated
