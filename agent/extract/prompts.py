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
from .schema import FAMILIES, FAMILY_SCHEMAS

PROMPT_VERSION = 2

SYSTEM = """\
You read a single page of a personal health record and report exactly what it says.

You are not a clinician and this is not a consultation. Do not diagnose, do not
suggest causes, do not say whether anything is concerning, and do not comment on
whether a dose looks right. Report what is on the page and stop.

You will be asked about the page in parts: first medications, then allergies,
then problems and the practitioners named. Answer only the part you are asked.

Rules, in order of importance:

1. UNSURE MEANS UNCLEAR. Whenever you cannot read something with certainty — a
   name, a strength, how often, a reaction, the whole page — put it in the
   unclear list and leave it out of the rest of your answer. That is the right
   answer, not a failure: it sends the page to the person to check. A guessed
   dose or a guessed drug name is the one answer that does harm here. If you
   cannot read the page at all, set readable to false.

2. A NAME IS ONLY A NAME. A medicine's name is the word or words that name it —
   "Atorvastatin", "Perindopril Arginine" — never "Atorvastatin 20mg tablets".
   The strength goes in strength and how often goes in frequency.

3. COPY, NEVER COMPUTE. Every value is copied off the page exactly as written.
   Do no arithmetic of any kind: no totals, no day counts, no dates worked out
   from other dates.

4. NEVER INVENT A DATE. If the page states a date plainly, copy it and say how
   precisely it is stated. If it refers to time in words — "around Easter" —
   copy the phrase into occurred_span and leave occurred_at null.

5. ONLY WHAT IS ON THIS PAGE. Do not add anything from what you know about these
   medicines, and do not complete a half-written instruction. A strength with no
   frequency on the page has no frequency.

Answer with JSON matching the schema you have been given. Nothing else."""

OPENING = """\
This is {page_of} of an artefact in the record.

First, the medications. Report every medicine you can read with certainty, with
its strength and how often it is taken copied exactly. Anything you cannot read
with certainty goes in unclear. If the page names no medicines, return an empty
list — that is a correct answer."""

FOLLOW_UPS = {
    "allergies": """\
Now the allergies on the same page. Report every allergy or reaction you can read
with certainty, and put anything you cannot read with certainty in unclear. If
there are none, return empty lists.""",
    "problems": """\
Now the conditions the page itself names as the person's, and the practitioners
it names. Report only what is written — never a condition worked out from a
result or a medicine. Anything you cannot read with certainty goes in unclear.
If there are none, return empty lists.""",
}

@dataclass(frozen=True)
class Prompt:
    """A rendered conversation about one page, and its identity.

    ``messages`` is the opening: the system prompt and the first question with
    the page attached. ``follow_ups`` are asked in order as later turns of the
    same conversation, which is what lets the server reuse the page it already
    read. The hash covers all of it, and every group's schema.
    """

    messages: tuple[dict[str, Any], ...]
    prompt_hash: str
    follow_ups: tuple[tuple[str, str], ...] = ()
    version: int = PROMPT_VERSION
    #: Whether the groups are asked with the voice-note schemas.
    transcript: bool = False

    def to_list(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self.messages]


def _canonical(value: Any) -> str:
    """Stable JSON. Sorted keys and fixed separators, so the hash is the text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_hash(system: str, user: str, schema: Mapping[str, Any]) -> str:
    """``sha256:…`` over the words and the schema together.

    Both, because a schema edit changes what was asked as surely as a wording
    change does, and a hash that missed it would let re-extraction think nothing
    had changed. *user* carries every turn's words and *schema* every group's.
    """
    digest = hashlib.sha256()
    digest.update(_canonical({"version": PROMPT_VERSION, "system": system, "user": user}).encode())
    digest.update(_canonical(dict(schema)).encode())
    return f"sha256:{digest.hexdigest()}"


def _conversation_hash(system: str, opening: str) -> str:
    words = "\n\n".join([opening, *(FOLLOW_UPS[f] for f in FAMILIES[1:])])
    return prompt_hash(system, words, FAMILY_SCHEMAS)


def build(page: PreparedImage, total_pages: int = 1) -> Prompt:
    """The conversation for one page. One page per prompt, always.

    Concatenating pages inflates the image budget, degrades reading accuracy, and
    destroys per-page citation granularity — a claim that cites a five-page
    discharge summary without saying which page is one a reader cannot check.
    """
    number = page.page or 1
    opening = OPENING.format(
        page_of=f"page {number} of {total_pages}" if total_pages > 1 else f"page {number}"
    )
    messages = (
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [image_part(page.media_type, page.base64), text_part(opening)],
        },
    )
    return Prompt(
        messages=messages,
        prompt_hash=_conversation_hash(SYSTEM, opening),
        follow_ups=tuple((f, FOLLOW_UPS[f]) for f in FAMILIES[1:]),
    )


def describe(prompts: Sequence[Prompt]) -> dict[str, Any]:
    """What provenance records about the prompts used for one artefact."""
    return {
        "prompt_version": PROMPT_VERSION,
        "prompt_hashes": sorted({prompt.prompt_hash for prompt in prompts}),
    }
