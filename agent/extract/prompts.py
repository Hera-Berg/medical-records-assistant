"""The prompt, and the hash that identifies it.

Every ``extraction.completed`` event records a ``prompt_hash``. It is part of the
idempotency key, so a retry produces no second claim; it is part of provenance,
so "why did the record change last Tuesday" is answerable a year later; and it is
what makes a prompt change re-extractable and diffable rather than silent.

The hash covers the **rendered** prompt and the schema together. A change to
either changes what the model was asked, and a hash that covered only the words
would let a schema edit slip past re-extraction unnoticed.

The instructions here repeat rules that are also enforced in code. That is not
redundancy to be tidied away: the prompt makes the model's job clear, and the
code makes the guarantee. Where they disagree the code wins, silently and by
construction — a model that assigns itself a consequence tier finds there is no
field for one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..llm.client import image_part, text_part
from .images import PreparedImage
from .schema import EXTRACTION_SCHEMA

PROMPT_VERSION = 1

SYSTEM = """\
You read a single page of a personal health record and report exactly what it says.

You are not a clinician and this is not a consultation. Do not diagnose, do not
suggest causes, do not say whether anything is concerning, and do not comment on
whether a dose looks right. Report what is on the page and stop.

Rules, in order of importance:

1. COPY, NEVER COMPUTE. Every value you report is a span of text copied off the
   page. "5mg daily" is reported as "5mg daily", never "5.0 mg once per day".
   Do no arithmetic of any kind: no totals, no day counts, no dates worked out
   from other dates. Quantities are copied as written and counted elsewhere.

2. NEVER INVENT A DATE. If the page states a date plainly, copy it and say how
   precisely it is stated — "June 2026" is month precision, not a day you have
   picked within June. If the page refers to time in words instead — "around
   Easter", "last Christmas", "the week before the wedding" — copy the phrase
   verbatim into occurred_span and leave occurred_at null. Do not work out what
   date the phrase means. Someone will be asked; a wrong guess is worse than the
   phrase.

3. IF YOU CANNOT READ IT, SAY SO. Set readable to false and say what stops you.
   An honest "I cannot read this" sends the page to a person. A plausible guess
   at a dose does not, and a wrong dose in a medical record is the specific harm
   this system exists to prevent.

4. REPORT ONLY WHAT IS ON THIS PAGE. Do not carry anything over from what you
   know about these drugs, and do not complete a partial instruction from
   experience. A script that gives no repeat count has no repeat count.

5. ONE PAGE. You are looking at a single page. Do not refer to earlier or later
   pages and do not assume what they contain.

Answer with JSON matching the schema you have been given. Nothing else."""

USER = """\
This is {page_of} of an artefact in the record.

Read it and report what it states, following the rules exactly. If it says
nothing about medications, allergies, problems or practitioners, return an empty
claims list — that is a correct answer, not a failure."""


@dataclass(frozen=True)
class Prompt:
    """A rendered prompt and its identity."""

    messages: tuple[dict[str, Any], ...]
    prompt_hash: str
    version: int = PROMPT_VERSION

    def to_list(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self.messages]


def _canonical(value: Any) -> str:
    """Stable JSON. Sorted keys and fixed separators, so the hash is the text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_hash(system: str, user: str, schema: Mapping[str, Any]) -> str:
    """``sha256:…`` over the words and the schema together.

    Both, because a schema edit changes what was asked as surely as a wording
    change does, and a hash that missed it would let re-extraction think nothing
    had changed.
    """
    digest = hashlib.sha256()
    digest.update(_canonical({"version": PROMPT_VERSION, "system": system, "user": user}).encode())
    digest.update(_canonical(dict(schema)).encode())
    return f"sha256:{digest.hexdigest()}"


def build(page: PreparedImage, total_pages: int = 1) -> Prompt:
    """The prompt for one page. One page per prompt, always.

    Concatenating pages inflates the image budget, degrades reading accuracy, and
    destroys per-page citation granularity — a claim that cites a five-page
    discharge summary without saying which page is one a reader cannot check.
    """
    number = page.page or 1
    user = USER.format(
        page_of=f"page {number} of {total_pages}" if total_pages > 1 else f"page {number}"
    )
    messages = (
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                image_part(page.media_type, page.base64),
                text_part(user),
            ],
        },
    )
    return Prompt(
        messages=messages,
        prompt_hash=prompt_hash(SYSTEM, user, EXTRACTION_SCHEMA),
    )


def describe(prompts: Sequence[Prompt]) -> dict[str, Any]:
    """What provenance records about the prompts used for one artefact."""
    return {
        "prompt_version": PROMPT_VERSION,
        "prompt_hashes": sorted({prompt.prompt_hash for prompt in prompts}),
    }
