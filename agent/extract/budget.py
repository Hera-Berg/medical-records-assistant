"""How much room one page's answer gets, and what happens when it runs out.

A prescription yields two or three claims. A pathology report yields four result
tables and a comment, and the same fixed ceiling that was generous for the first
cuts the second off mid-object. The answer is not a bigger constant — a constant
large enough for the worst page is wasteful on every ordinary one and still
guesses — and it is not a config key either: the person who would have to find
and raise that key is exactly the person who does not yet know that a token cap
is what they are looking at.

So the ceiling is **raised on evidence**. The server says ``finish_reason:
"length"``; the page is asked again with more room. The ladder doubles from
:data:`agent.llm.client.DEFAULT_MAX_TOKENS` and stops at :data:`CEILING`, and
every step is clamped to what the configured context window can actually hold,
computed from the prompt tokens the server itself reported for the attempt that
overran.

Three properties this keeps:

**It is deterministic.** The ladder is fixed, the decision that advances it comes
from the server rather than from a clock or a random draw, and the same page with
the same prompt walks the same steps. Byte-identical rebuild replays events; it
never re-asks a model. But a retry path that wandered would still be the wrong
shape for this codebase.

**It never splits a page.** ``MODELS.md`` settles one page per prompt: splitting
further would mean sectioning a single page's text, which inflates nothing but
does destroy the guarantee that a claim cites the page it came from, and leaves
two half-answers nothing reconciles. When a single page overruns the top of the
ladder, that is reported as its own outcome — never dropped, never partially
read.

**It stops.** A page that still overruns at the ceiling, or has no room left in
``ctx``, is a page for a person. Retrying it at temperature zero produces the
same cut-off answer, so it parks rather than looping.
"""

from __future__ import annotations

from ..llm.client import DEFAULT_MAX_TOKENS

START = DEFAULT_MAX_TOKENS

#: The top of the ladder. Eight thousand tokens of claims is a page reporting
#: dozens of values — beyond that the answer is more likely to be a model that
#: has started repeating itself than a document with more to say, and either way
#: it wants a person rather than a bigger number.
CEILING = 8192

#: Tokens held back inside ``ctx`` so the window itself is not what cuts the
#: answer off. Small: the point is to leave the model room to close its JSON.
MARGIN = 256

#: The smallest raise worth a second request. Asking again for eighty more
#: tokens costs a whole vision prefill and will almost certainly be cut off in
#: the same place; a page that close to the edge of ``ctx`` wants a bigger
#: window or a smaller image, which is what the exhausted message says.
MIN_GAIN = 512


def room_in_context(ctx: int, prompt_tokens: int | None) -> int:
    """How many output tokens ``ctx`` can still hold after the prompt.

    *prompt_tokens* comes from the server's own usage report for the attempt
    that overran, so this is measured rather than estimated. With no usage
    reported it falls back to the whole window less the margin, which can be
    optimistic — the failure that causes is one more truncated answer and one
    more rung of the ladder, not a wrong claim.
    """
    return max(0, ctx - MARGIN - max(0, prompt_tokens or 0))


def next_budget(current: int, ctx: int, prompt_tokens: int | None) -> int | None:
    """The next ``max_tokens`` to try after *current* was cut off, or ``None``.

    ``None`` means there is no more room to give: either the ladder is at its
    ceiling, or the context window is full. Both send the page to a person, and
    the caller says which.
    """
    room = room_in_context(ctx, prompt_tokens)
    wanted = min(current * 2, CEILING, room)
    return wanted if wanted - current >= MIN_GAIN else None


def describe_raise(previous: int, nxt: int) -> str:
    """The note recorded against the extraction when the ceiling was raised."""
    return (
        f"the answer was cut off at {previous} tokens, so it was asked again with "
        f"room for {nxt} — the same prompt, only a higher ceiling"
    )


def describe_exhausted(current: int, ctx: int, prompt_tokens: int | None) -> str:
    """Why no further attempt was made, in terms of which limit was reached."""
    room = room_in_context(ctx, prompt_tokens)
    if current >= CEILING:
        return (
            f"There is no more room to give: {CEILING} tokens is the top of the "
            f"ladder, and a single page that states more than that is one to read "
            f"by hand. One page per prompt is already the rule, and reading half "
            f"of a page would file a partial list as though it were the whole of it."
        )
    took = prompt_tokens if prompt_tokens is not None else "most"
    return (
        f"There is no more room to give: models.vlm.ctx is {ctx} and this page's "
        f"prompt took {took} of it, leaving {room}. A larger ctx on the box, or a "
        f"smaller max_pixels so the image costs fewer tokens, is what makes room."
    )
