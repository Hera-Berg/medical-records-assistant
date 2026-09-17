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


#: How long the reader may take per output token when nothing faster has been
#: measured. Deliberately slow: this only sizes a timeout, and a timeout too
#: short reports a reader still writing as one that went away.
SLOWEST_TOKENS_PER_SECOND = 2.0
#: Headroom on the generation allowance. Speed wanders with what else the
#: machine is doing; a timeout sized to the average cuts off the slow half.
SPEED_HEADROOM = 1.5

#: Generation speed last reported by each model's own server, by identity.
_OBSERVED_RATES: dict[str, float] = {}


def _rate_for(client) -> float | None:
    """Tokens per second this model generates at, observed or measured, if known."""
    model = client.settings.model
    observed = _OBSERVED_RATES.get(model)
    if observed:
        return observed
    runtime = getattr(client, "runtime", None) or {}
    if runtime.get("kind") != "bundled":
        return None
    from ..runtime import measurements, platforms  # noqa: PLC0415 - import cycle

    speed = measurements.for_model(model).speed.get(platforms.current() or "")
    if speed is not None and speed.generation_tokens_per_second:
        return speed.generation_tokens_per_second
    return SLOWEST_TOKENS_PER_SECOND


def read_timeout_for(client, max_tokens: int) -> float | None:
    """The read timeout for an answer of up to *max_tokens*.

    The configured timeout covers reading the prompt and the page; on top of it
    goes the time the ceiling itself takes at the speed this model has been seen
    to write, with headroom. Before this the whole ladder shared one timeout, and
    on a laptop CPU writing at nine tokens a second the top rung could not finish
    inside it — a reader still writing was reported as a machine that had gone to
    sleep. ``None`` for a server that reports no speed and was never measured:
    the configured timeout stands, as it always did.
    """
    rate = _rate_for(client)
    if not rate:
        return None
    return client.settings.read_timeout_s + (max_tokens / rate) * SPEED_HEADROOM


def _observe(client, completion) -> None:
    timings = completion.raw.get("timings") if isinstance(completion.raw, dict) else None
    rate = timings.get("predicted_per_second") if isinstance(timings, dict) else None
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
        previous = _OBSERVED_RATES.get(client.settings.model)
        # The slower of the two: a timeout sized to a fast moment cuts off a slow one.
        _OBSERVED_RATES[client.settings.model] = min(float(rate), previous) if previous else float(rate)


def is_run_on(text: str) -> bool:
    """Whether a cut-off answer ends in the same passage written over and over.

    A page states a bounded number of things; an answer still going after
    thousands of tokens is almost always a model repeating itself, and giving it
    more room buys a longer repetition. Checked on the tail: some stretch of 8 to
    300 characters repeated at least four times back to back, covering at least
    300 characters. An answer that is merely long does not look like that.
    """
    tail = text[-3000:]
    length = len(tail)
    for period in range(8, 301):
        for offset in range(period):
            end = length - offset
            if end < period * 4:
                break
            unit = tail[end - period:end]
            if not unit.strip():
                continue
            count, position = 1, end - period
            while position >= period and tail[position - period:position] == unit:
                count += 1
                position -= period
            if count >= 4 and count * period >= 300:
                return True
    return False


def describe_run_on() -> str:
    return (
        "It was writing the same thing over and over instead of finishing, so it "
        "was stopped rather than given more room — more room only makes a longer "
        "repetition. Nothing is proposed from it. Photograph the document again, "
        "or check it yourself."
    )


def ask(client, messages, schema, schema_name: str, where: str | None = None):
    """Ask once, and again with more room while the box says it ran out.

    The one implementation of the ladder. Extraction and the eval harness both
    call this, so the harness scores what extraction actually does — an eval
    that asked once at a fixed ceiling scored a recording as a failure that
    real extraction would have read on its second call.

    Returns the final completion and the notes recording each raise.
    """
    import logging  # noqa: PLC0415

    ceiling = START
    notes: list[str] = []
    prefix = f"{where}: " if where else ""
    while True:
        timeout = read_timeout_for(client, ceiling)
        extra = {"read_timeout_s": timeout} if timeout is not None else {}
        completion = client.complete(
            messages, schema=schema, schema_name=schema_name, max_tokens=ceiling, **extra
        )
        _observe(client, completion)
        if not completion.truncated:
            return completion, tuple(notes)
        if is_run_on(completion.content):
            # More room buys a longer repetition. Stop here, with the answer that
            # shows it, and let the reader say why.
            return completion, tuple(notes)
        nxt = next_budget(ceiling, client.settings.ctx, completion.prompt_tokens)
        if nxt is None:
            return completion, tuple(notes)
        logging.getLogger("agent.extract").info(
            "answer cut off at %d tokens%s; asking again with %d",
            ceiling, f" ({where})" if where else "", nxt,
        )
        notes.append(prefix + describe_raise(ceiling, nxt))
        ceiling = nxt


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
