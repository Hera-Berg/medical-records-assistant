"""The only place that knows an inference server's API.

The client speaks OpenAI-compatible ``/chat/completions`` against a configured
base URL and that is the entire coupling. Whether the far end is ``mlx-vlm``,
``llama-server``, vLLM or Ollama is not this module's business, and no
model-specific code exists anywhere else in the package.

Four things here are load-bearing rather than incidental.

**Order: check the address, then attach the credential.** :meth:`_headers` takes
the verified addresses as an argument it cannot fabricate, so if the guard ever
fails open the key still does not leave the machine.

**Failure kinds are distinct, and one of them is terminal.** A ``401`` is not a
network problem. Retrying a rotated key achieves nothing and may trip lockout,
so it raises :class:`AuthRejected`, which the queue treats as a park rather than
a backoff. Collapsing that into "offline" is how a rotated key looks like a
sleeping Mac and nobody investigates for a week.

**The response's ``model`` field is checked every time.** A remote server can be
swapped under the client without it noticing, which would silently corrupt
provenance; a disagreement stops rather than proceeding, and never updates the
config to match.

**Running out of room is not the same as answering badly.** ``finish_reason``
is carried on every completion and checked before anything parses the content. A
cut-off answer is reported as a cut-off answer — a cap to raise — rather than as
invalid JSON, which is a server's grammar setting to check. The two look
identical at the parser and want opposite investigations.

**Grammar-constrained decoding is not optional.** Without it these models narrate
a plan before answering, that narration lands in ``content``, and every
schema-validated extraction fails. A stray ``<think>`` block is still stripped
defensively, and the fact recorded, because failing every extraction silently is
worse than un-wrapping an envelope.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import httpx

from ..errors import (
    AuthRejected,
    EndpointUnreachable,
    InferenceError,
    ModelIdentityMismatch,
    OutputTruncated,
    RateLimited,
)
from . import credentials as credentials_mod
from . import redaction
from .settings import VlmSettings

log = logging.getLogger("agent.llm")

CHAT_PATH = "/chat/completions"
MODELS_PATH = "/models"

#: Room for one page's answer, before anything asks for more. Enough for a
#: prescription several times over; not enough for a pathology report with four
#: result tables, which is why :mod:`agent.extract.budget` raises it when the
#: server says an answer was cut off rather than finished.
DEFAULT_MAX_TOKENS = 2048

#: One field, one word. Small enough that a server honouring the schema cannot
#: overrun a tiny ceiling, which is what makes the ceiling itself a signal: an
#: answer still going after 64 tokens is an answer nothing is constraining.
GRAMMAR_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"word": {"type": "string"}},
    "required": ["word"],
    "additionalProperties": False,
}
GRAMMAR_PROBE_NAME = "grammar_probe"
GRAMMAR_PROBE_TOKENS = 64

#: A reasoning trace that reached ``content`` because no parser was configured.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

#: Servers that reject the thinking toggle say so in the error body. Matching on
#: the field name rather than the status keeps this from swallowing real 400s.
_UNKNOWN_FIELD = re.compile(
    r"chat_template_kwargs|enable_thinking|unexpected keyword|unknown field",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Completion:
    """One answer, and everything provenance needs to record about it."""

    content: str
    model: str
    raw: dict[str, Any]
    latency_s: float
    #: Set when a reasoning trace had to be stripped from ``content``. Recorded
    #: in the extraction event: it means the server has no reasoning parser and
    #: is one server-config change away from failing differently.
    stripped_reasoning: bool = False
    sampling: Mapping[str, Any] = field(default_factory=dict)
    #: What the server said stopped it: ``stop``, ``length``, or whatever else
    #: it uses. Recorded on the extraction event, and checked before the content
    #: is parsed.
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """Whether the answer was cut off at the token ceiling rather than ended."""
        return self.finish_reason == "length"

    @property
    def max_tokens(self) -> int | None:
        """The ceiling this answer was given, for the message that reports it."""
        value = self.sampling.get("max_tokens")
        return value if isinstance(value, int) else None

    @property
    def usage(self) -> dict[str, Any]:
        usage = self.raw.get("usage")
        return dict(usage) if isinstance(usage, dict) else {}

    @property
    def prompt_tokens(self) -> int | None:
        value = self.usage.get("prompt_tokens")
        return value if isinstance(value, int) else None


def image_part(media_type: str, base64_data: str) -> dict[str, Any]:
    """An ``image_url`` content part, the shape every OpenAI-compatible server takes.

    Support for these is less uniform across MLX servers than across llama.cpp —
    ``mlx_lm.server`` is text-only and will answer text prompts perfectly while
    discarding every image. That reads as "the model is bad at OCR", which is why
    :meth:`Client.probe_vision` exists.
    """
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{base64_data}"},
    }


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class Client:
    """A configured connection to one inference endpoint."""

    def __init__(
        self,
        settings: VlmSettings,
        vault_root=None,
        transport: httpx.BaseTransport | None = None,
        resolver=None,
        credential: Callable[[], credentials_mod.Credential] | None = None,
        runtime: Mapping[str, Any] | None = None,
    ):
        """*credential* replaces the resolver chain outright, when it is given.

        It exists for the reader on this computer, whose key is made per launch
        and lives only in memory. Falling through to the chain would be the one
        wrong answer: the chain ends in the keychain, which holds the *remote*
        box's key, and would send it to whatever is listening on a loopback port.

        *runtime* is what this client talks to, as observations for provenance —
        ``kind``, ``engine``, pinned file hashes. The client never reads it.
        """
        self.settings = settings
        self.vault_root = vault_root
        self._resolver = resolver
        self._credential = credential
        self.runtime: dict[str, Any] = dict(runtime) if runtime else {"kind": "endpoint"}
        redaction.install()
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_s,
            read=settings.read_timeout_s,
            write=settings.connect_timeout_s,
            pool=settings.connect_timeout_s,
        )
        # verify stays on. Tailscale Serve presents a real certificate, so there
        # is never a reason to disable it, and a flag that can be set to False
        # is a flag that gets set to False.
        self._http = httpx.Client(timeout=timeout, transport=transport, verify=True)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the guard, and only then the key ---------------------------------

    def _verified_addresses(self) -> tuple[str, ...]:
        endpoint = self.settings.endpoint
        if self._resolver is not None:
            return endpoint.verify(self._resolver)
        return endpoint.verify()

    def _headers(self, addresses: Sequence[str]) -> dict[str, str]:
        """Build the auth header, having been handed proof of a private address.

        *addresses* is required and re-checked. It cannot be fabricated by a
        caller that skipped the guard, which is the point: MODELS.md asks that
        the ordering hold even if the guard fails open.
        """
        from .endpoint import is_private  # noqa: PLC0415 - avoids an import cycle

        if not addresses or not all(is_private(address) for address in addresses):
            raise InferenceError(
                "refusing to attach a credential to an endpoint that has not been "
                "verified as private. This should be unreachable; if you are seeing "
                "it, the address guard has a bug and the key has not left the machine."
            )
        credential = self.resolve_credential()
        redaction.register(credential.value)
        name, value = self.settings.auth.format(credential.value)
        return {name: value, "Content-Type": "application/json"}

    def resolve_credential(self) -> credentials_mod.Credential:
        """The key this client would send, from its own source. Never logged."""
        if self._credential is not None:
            return self._credential()
        return credentials_mod.resolve(self.settings.auth.api_key_env, self.vault_root)

    # -- requests ----------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        addresses = self._verified_addresses()
        headers = self._headers(addresses)
        url = self.settings.endpoint.url(path)
        try:
            response = self._http.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise EndpointUnreachable(
                f"the inference endpoint timed out: {redaction.scrub(str(exc))}. The "
                f"box may be asleep or off the tailnet; the job stays queued."
            ) from None
        except httpx.HTTPError as exc:
            raise EndpointUnreachable(
                f"cannot reach the inference endpoint at "
                f"{self.settings.endpoint.base_url}: {redaction.scrub(str(exc))}. The "
                f"job stays queued and will drain when the box is back."
            ) from None
        return self._read(response)

    def _get(self, path: str) -> dict[str, Any]:
        addresses = self._verified_addresses()
        headers = self._headers(addresses)
        url = self.settings.endpoint.url(path)
        try:
            response = self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise EndpointUnreachable(
                f"cannot reach the inference endpoint: {redaction.scrub(str(exc))}"
            ) from None
        return self._read(response)

    def _read(self, response: httpx.Response) -> dict[str, Any]:
        """Classify the answer. The status code decides what kind of failure."""
        if response.status_code in (401, 403):
            raise AuthRejected(
                f"authentication rejected by the inference box "
                f"(HTTP {response.status_code}) — the key may have rotated. This is "
                f"not a network problem and is not retried: set a new key with "
                f"`health-agent set-key`, then `health-agent extract --resume`."
            )
        if response.status_code == 429:
            raise RateLimited(
                "the inference box is rate-limiting this client (HTTP 429). Backing "
                "off — but a box rate-limiting its own owner usually means something "
                "else is hammering it, so it is worth looking at what."
            )
        if response.status_code >= 500:
            raise EndpointUnreachable(
                f"the inference box returned HTTP {response.status_code}. Treated as "
                f"transient; the job stays queued."
            )
        if response.status_code >= 400:
            raise InferenceError(
                f"the inference box refused the request (HTTP "
                f"{response.status_code}): {redaction.scrub(_body_excerpt(response))}"
            )
        try:
            return response.json()
        except ValueError:
            raise InferenceError(
                f"the inference box returned something that is not JSON: "
                f"{redaction.scrub(_body_excerpt(response))}"
            ) from None

    # -- the one call that matters ----------------------------------------

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        schema: Mapping[str, Any] | None = None,
        schema_name: str = "extraction",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        """One chat completion, grammar-constrained when *schema* is given."""
        settings = self.settings
        sampling = {
            "temperature": settings.temperature,
            "presence_penalty": settings.presence_penalty,
            "max_tokens": max_tokens,
        }
        body: dict[str, Any] = {
            "model": settings.model,
            "messages": list(messages),
            "stream": False,
            **sampling,
        }
        if settings.max_pixels:
            # Explicit rather than relying on server defaults: the image, not the
            # prompt, is what blows the context and the RAM budget.
            body["max_pixels"] = settings.max_pixels
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": dict(schema),
                    "strict": True,
                },
            }
        if not settings.thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        started = time.monotonic()
        try:
            payload = self._post(CHAT_PATH, body)
        except InferenceError as exc:
            payload = self._retry_without_thinking_toggle(body, exc)
        latency = time.monotonic() - started

        content, stripped, finish_reason = _content_of(payload)
        reported = payload.get("model")
        self._check_identity(reported)
        return Completion(
            content=content,
            model=str(reported),
            raw=payload,
            latency_s=latency,
            stripped_reasoning=stripped,
            sampling=sampling,
            finish_reason=finish_reason,
        )

    def _retry_without_thinking_toggle(
        self, body: dict[str, Any], exc: InferenceError
    ) -> dict[str, Any]:
        """Some servers 400 on ``chat_template_kwargs``. Retry once without it.

        Narrow on purpose: only a request that carried the toggle, only when the
        server's own message names the field, and only once. Anything else is a
        real refusal and is re-raised — a retry loop that swallows 400s would
        hide a malformed schema behind a slow failure.
        """
        if "chat_template_kwargs" not in body or not _UNKNOWN_FIELD.search(str(exc)):
            raise exc
        log.warning(
            "the inference server rejected chat_template_kwargs; retrying without it. "
            "Thinking may be on: if extractions start failing validation, set a "
            "reasoning parser on the server."
        )
        retried = {k: v for k, v in body.items() if k != "chat_template_kwargs"}
        return self._post(CHAT_PATH, retried)

    def _check_identity(self, reported: object) -> None:
        if not isinstance(reported, str) or not reported.strip():
            raise ModelIdentityMismatch(
                "the inference box did not say which model answered. 'Which model "
                "produced this claim' has to be answerable a year later, so the "
                "extraction is not recorded."
            )
        if reported.strip() != self.settings.model:
            raise ModelIdentityMismatch(
                f"the inference box is running {reported.strip()!r}, but config.toml "
                f"pins {self.settings.model!r}. Stopping rather than recording claims "
                f"against the wrong model.\n\n"
                f"If the box was deliberately changed, edit models.vlm.model yourself "
                f"and re-extract with `health-agent rebuild --reextract`. This is "
                f"never updated automatically: a config file describing a server's "
                f"past state is worse than no record."
            )

    # -- startup checks ----------------------------------------------------

    def list_models(self) -> list[str]:
        payload = self._get(MODELS_PATH)
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [
            str(entry["id"])
            for entry in data
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        ]

    def probe_vision(self, media_type: str, base64_data: str, expected: str) -> bool:
        """Send a fixture image with known text and check the answer contains it.

        The failure this catches is a server that accepts ``image_url`` parts and
        silently discards them: it answers text prompts perfectly and quietly
        ignores every prescription photo, which presents as "the model is bad at
        OCR" and wastes an evening. The same probe covers the llama.cpp path,
        where the equivalent trap is loading weights without the ``mmproj``
        projector.
        """
        answer = self.complete(
            [
                {
                    "role": "user",
                    "content": [
                        image_part(media_type, base64_data),
                        text_part(
                            "Transcribe every word visible in this image. Reply with "
                            "the words only."
                        ),
                    ],
                }
            ],
            max_tokens=128,
        )
        return expected.lower() in answer.content.lower()

    def probe_grammar(self) -> Completion:
        """Ask for one tiny schema-constrained object, and hand back the answer.

        The assertions live in :mod:`agent.extract.probe`, which has the words
        for what each failure means. What matters here is that the request is
        *shaped like an extraction* — ``response_format`` with a strict schema —
        because the thing being checked is whether this server honours that at
        all, or only its own guided-decoding toggle. A server that ignores the
        field answers text prompts perfectly and fails every real extraction,
        and until this probe existed that failure was only ever discovered by a
        ninety-second job on a prescription photo.
        """
        return self.complete(
            [
                {
                    "role": "user",
                    "content": (
                        "Reply with JSON only, matching the schema you have been "
                        "given: the field `word` set to the string `ready`."
                    ),
                }
            ],
            schema=GRAMMAR_PROBE_SCHEMA,
            schema_name=GRAMMAR_PROBE_NAME,
            max_tokens=GRAMMAR_PROBE_TOKENS,
        )


def _content_of(payload: Mapping[str, Any]) -> tuple[str, bool, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InferenceError("the inference box returned no choices")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message")
    if not isinstance(message, dict):
        raise InferenceError("the inference box returned a choice with no message")
    content = message.get("content")
    if not isinstance(content, str):
        raise InferenceError("the inference box returned a message with no text")
    finish = choice.get("finish_reason")
    cleaned = _THINK.sub("", content).strip()
    return cleaned, cleaned != content.strip(), finish if isinstance(finish, str) else None


def _body_excerpt(response: httpx.Response, limit: int = 400) -> str:
    try:
        text = response.text
    except Exception:  # pragma: no cover - a body that cannot be decoded
        return "<unreadable body>"
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_completion(completion: Completion) -> Any:
    """The JSON a completion carries — refusing a truncated one before parsing it.

    The order is the point. A cut-off answer sometimes parses: a server can stop
    mid-array and leave something structurally valid but short, and the claims
    it dropped are the ones nobody would ever notice were missing. So the
    ceiling is checked first, and the content is not read at all.

    The raw output is still recorded by the caller either way. Nothing is thrown
    away; nothing is made a claim of.
    """
    if completion.truncated:
        ceiling = completion.max_tokens
        room = f" at the {ceiling}-token ceiling" if ceiling else ""
        raise OutputTruncated(
            f"the box stopped{room} with the answer unfinished (finish_reason "
            f"'length'), so the JSON is cut off mid-object. That is a token cap, "
            f"not a grammar problem — nothing needs changing on the server. The "
            f"raw output is recorded exactly as it came back; no claim is made "
            f"from it, because a truncated answer is a partial list, and a "
            f"silently short list of medications is the one failure this record "
            f"exists to prevent."
        )
    return parse_json_content(completion.content)


def parse_json_content(content: str) -> Any:
    """Read the model's answer as JSON, or raise.

    Never repairs. ``MODELS.md``: "Do not post-process free text into JSON with a
    regex." Grammar-constrained decoding is what makes this succeed; when it
    fails, the raw output is recorded and no claim is emitted.
    """
    try:
        return json.loads(content)
    except ValueError as exc:
        raise InferenceError(
            f"the model's answer is not JSON ({exc}). Grammar-constrained decoding "
            f"should make this impossible — check that the server supports "
            f"response_format json_schema. The raw output is recorded; no claim is "
            f"made from it."
        ) from None
