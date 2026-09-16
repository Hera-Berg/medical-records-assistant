"""Finding out what state the inference box is actually in, before a job does.

``MODELS.md``: "On startup, probe with a cheap authenticated request so all three
states — unreachable, unauthorised, working — are known before the first real job
runs." Discovering unreachability by failing a ninety-second job is how a
five-second answer costs an afternoon.

The checks run in a fixed order, each depending on the last, and stop at the
first failure. That ordering is the same one the client enforces: the address is
verified before a credential is read, so a probe against a public host never
touches the key either.

Two checks earn their place beyond reachability, and they fail in ways nothing
else catches.

The **grammar check** sends one tiny schema-constrained request and asserts three
things about the answer: that it parses, that it is the shape it was given, and
that ``finish_reason`` is ``stop`` rather than ``length``. Some servers apply
guided decoding only through their own toggle and ignore ``response_format``
entirely; the effect is an unconstrained answer that fails every extraction with
a parse error, and until this check existed the only way to discover it was a
ninety-second job on a prescription photo. The ceiling is deliberately tiny, so
an answer that runs past it is itself the signal: a one-field object cannot
overrun sixty-four tokens unless nothing is constraining it.

The **vision check is the one that earns its place**. Support for ``image_url``
content parts is less uniform across MLX servers than across llama.cpp —
``mlx_lm.server`` is text-only and will answer every text prompt perfectly while
silently discarding every image. That reads as "the model is bad at OCR" and can
cost an evening. So the probe sends a generated image containing known text and
fails loudly if the answer does not contain it. The same check covers the
llama.cpp path, where the equivalent trap is loading weights without the separate
``mmproj`` projector.

Nothing here reports a credential, a prefix of one, or its length. ``auth`` is
``ok``, ``failed`` or ``missing`` and nothing more.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from ..errors import (
    AuthRejected,
    CredentialError,
    EndpointNotPrivate,
    EndpointUnreachable,
    InferenceError,
    ModelIdentityMismatch,
)
from ..llm import credentials as credentials_mod
from ..llm import redaction
from ..llm.endpoint import FUNNEL_WARNING
from .images import DESKEW_NOTE

#: Deliberately not a real word from anybody's record. It has to be unusual
#: enough that a model cannot produce it by guessing what a prescription says.
PROBE_TEXT = "ZQ7 VERIFY 42"

#: What the grammar probe asks the model to put in its one field.
GRAMMAR_WORD = "ready"

WORKING = "working"
UNREACHABLE = "unreachable"
UNAUTHORISED = "unauthorised"
MISCONFIGURED = "misconfigured"
BLIND = "vision-not-working"
#: The server answers, but does not constrain decoding to the schema it was
#: given. Its own state for the same reason ``BLIND`` is: every extraction fails
#: and the message it fails with points at the wrong thing.
UNCONSTRAINED = "grammar-not-enforced"


def probe_image() -> bytes:
    """A generated image carrying :data:`PROBE_TEXT`. Never stored in the vault."""
    image = Image.new("RGB", (640, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 80), PROBE_TEXT, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""

    def describe(self) -> str:
        return f"{'ok  ' if self.ok else 'FAIL'}  {self.name}: {self.detail}".rstrip()


@dataclass(frozen=True)
class ProbeReport:
    """The three states, plus what was checked to get there."""

    state: str
    checks: tuple[Check, ...] = ()
    auth: str = "missing"
    model_reported: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.state == WORKING

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            # `ok | failed | missing`, and nothing more. Never the key, never a
            # prefix of it, never its length.
            "auth": self.auth,
            "model_reported": self.model_reported,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
            "notes": list(self.notes),
        }


def _check_grammar(answer) -> Check:
    """What one tiny schema-constrained answer proves.

    Each failure gets its own sentence because each sends the reader somewhere
    different: a cut-off answer to the token ceiling, an unparseable one to the
    server's guided-decoding setting, and a well-formed answer of the wrong
    shape to whether the schema crossed the wire at all.
    """
    from ..llm.client import GRAMMAR_PROBE_TOKENS, parse_json_content  # noqa: PLC0415

    if answer.truncated:
        return Check(
            "grammar",
            False,
            f"the box was still answering at {GRAMMAR_PROBE_TOKENS} tokens for a "
            f"one-field object, which it could not be if the schema were being "
            f"applied. Most likely response_format is ignored and the reply is "
            f"prose — or a reasoning trace — rather than JSON. Turn on this "
            f"server's guided-grammar option, and set a reasoning parser or "
            f"disable thinking",
        )
    try:
        payload = parse_json_content(answer.content)
    except InferenceError:
        return Check(
            "grammar",
            False,
            "the box answered a schema-constrained request with something that is "
            "not JSON. It accepts response_format without applying it, so every "
            "extraction would fail this way. Turn on guided grammar on the server",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("word"), str):
        return Check(
            "grammar",
            False,
            "the box answered with JSON that is not the schema it was given — the "
            "one required field is missing. Decoding is not being constrained, so "
            "claim extraction would produce valid JSON of the wrong shape, which "
            "the validator rejects one claim at a time",
        )
    return Check(
        "grammar",
        True,
        f"a schema-constrained request came back as the schema, and finished "
        f"({answer.finish_reason or 'no finish_reason reported'})",
    )


def run(client, skip_vision: bool = False) -> ProbeReport:
    """Work through the checks in order, stopping at the first that fails."""
    checks: list[Check] = []
    settings = client.settings
    notes = [FUNNEL_WARNING, DESKEW_NOTE]

    # 1. The address. Before anything else, and before the key is read.
    try:
        addresses = client._verified_addresses()
    except EndpointNotPrivate as exc:
        checks.append(Check("endpoint", False, redaction.scrub(str(exc))))
        return ProbeReport(MISCONFIGURED, tuple(checks), auth="missing", notes=tuple(notes))
    checks.append(
        Check(
            "endpoint",
            True,
            f"{settings.endpoint.base_url} resolves to {', '.join(addresses)} — private",
        )
    )

    # 2. The credential exists and is not empty. Resolved, never reported.
    #
    # `resolve` is called rather than `status` because its refusals name every
    # place that was looked in and what to do next, and this is the one command
    # whose whole job is telling a person what is wrong. `status` returns a
    # single word and exists for `/api/health`, which may say no more than that.
    try:
        found = credentials_mod.resolve(settings.auth.api_key_env, client.vault_root)
    except CredentialError as exc:
        checks.append(Check("credential", False, redaction.scrub(str(exc))))
        return ProbeReport(MISCONFIGURED, tuple(checks), auth="missing", notes=tuple(notes))
    # The source, never the value. `Credential.source` is a place, not a secret.
    checks.append(Check("credential", True, f"found in {found.source}"))

    # 3. A cheap authenticated request, which separates the three states.
    try:
        available = client.list_models()
    except AuthRejected as exc:
        checks.append(Check("authentication", False, redaction.scrub(str(exc))))
        return ProbeReport(UNAUTHORISED, tuple(checks), auth="failed", notes=tuple(notes))
    except EndpointUnreachable as exc:
        checks.append(Check("reachability", False, redaction.scrub(str(exc))))
        return ProbeReport(UNREACHABLE, tuple(checks), auth="ok", notes=tuple(notes))
    except (CredentialError, InferenceError) as exc:
        checks.append(Check("reachability", False, redaction.scrub(str(exc))))
        return ProbeReport(MISCONFIGURED, tuple(checks), auth="ok", notes=tuple(notes))
    checks.append(Check("authentication", True, "the box accepted the key"))

    if available and settings.model not in available:
        checks.append(
            Check(
                "model",
                False,
                f"config.toml pins {settings.model!r}, and the box offers "
                f"{', '.join(sorted(available))}. Copy the id verbatim from "
                f"/v1/models — it is checked on every call, so an approximation "
                f"stops the run rather than being ignored",
            )
        )
        return ProbeReport(MISCONFIGURED, tuple(checks), auth="ok", notes=tuple(notes))
    checks.append(Check("model", True, f"{settings.model} is available"))

    # 4. Guided decoding is actually applied. Cheap, text-only, and it comes
    #    before vision because a server that ignores the schema explains a great
    #    deal else besides, and costs one small request to find out about.
    #
    #    The box going to sleep between two checks is not a grammar failure, so
    #    the states that mean something else keep their own names here.
    try:
        answered = client.probe_grammar()
    except AuthRejected as exc:
        checks.append(Check("authentication", False, redaction.scrub(str(exc))))
        return ProbeReport(UNAUTHORISED, tuple(checks), auth="failed", notes=tuple(notes))
    except EndpointUnreachable as exc:
        checks.append(Check("reachability", False, redaction.scrub(str(exc))))
        return ProbeReport(UNREACHABLE, tuple(checks), auth="ok", notes=tuple(notes))
    except ModelIdentityMismatch as exc:
        checks.append(Check("model identity", False, redaction.scrub(str(exc))))
        return ProbeReport(MISCONFIGURED, tuple(checks), auth="ok", notes=tuple(notes))
    except InferenceError as exc:
        checks.append(Check("grammar", False, redaction.scrub(str(exc))))
        return ProbeReport(UNCONSTRAINED, tuple(checks), auth="ok", notes=tuple(notes))

    # What the box said it was, taken from the answer rather than from the
    # config. The client has already refused any disagreement, so this is the
    # identity of the machine that actually replied — which is what `/api/health`
    # and the settings screen report, and what makes "which model produced this
    # claim" answerable from a screen as well as from the log.
    reported = answered.model

    grammar = _check_grammar(answered)
    checks.append(grammar)
    if not grammar.ok:
        return ProbeReport(
            UNCONSTRAINED, tuple(checks), auth="ok", model_reported=reported,
            notes=tuple(notes),
        )

    if skip_vision:
        return ProbeReport(
            WORKING, tuple(checks), auth="ok", model_reported=reported,
            notes=tuple(notes),
        )

    # 5. Vision actually works. The check this module exists for.
    import base64  # noqa: PLC0415 - only needed here

    encoded = base64.b64encode(probe_image()).decode("ascii")
    try:
        saw_it = client.probe_vision("image/png", encoded, PROBE_TEXT)
    except ModelIdentityMismatch as exc:
        checks.append(Check("model identity", False, redaction.scrub(str(exc))))
        return ProbeReport(MISCONFIGURED, tuple(checks), auth="ok", notes=tuple(notes))
    except InferenceError as exc:
        checks.append(Check("vision", False, redaction.scrub(str(exc))))
        return ProbeReport(
            UNREACHABLE, tuple(checks), auth="ok", model_reported=reported,
            notes=tuple(notes),
        )

    if not saw_it:
        checks.append(
            Check(
                "vision",
                False,
                f"the box answered but did not read {PROBE_TEXT!r} out of a test "
                f"image. It is most likely discarding image content parts — "
                f"`mlx_lm.server` is text-only; you want an `mlx-vlm` server. On "
                f"llama.cpp the equivalent is loading the weights without the "
                f"separate mmproj projector file. Every prescription photo would "
                f"be silently ignored, which reads as the model being bad at OCR",
            )
        )
        return ProbeReport(
            BLIND, tuple(checks), auth="ok", model_reported=reported,
            notes=tuple(notes),
        )

    checks.append(Check("vision", True, f"the box read {PROBE_TEXT!r} out of a test image"))
    return ProbeReport(
        WORKING, tuple(checks), auth="ok", model_reported=reported, notes=tuple(notes)
    )
