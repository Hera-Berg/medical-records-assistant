"""The ``[models]`` tables in ``config.toml``, validated.

Kept out of :mod:`agent.config` so that module stays about the vault and the
secret scan, and kept out of :mod:`agent.llm.client` so the client stays about
the wire format. This is plain data: field names and ranges, no knowledge of any
server's API.

Every artefact is **pinned exactly, never by a floating tag**. Local model files
are pinned by ``sha256``; the remote model cannot be, so it is pinned by the
identity string the server reports and verified on every call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..errors import ConfigError, EndpointNotConfigured
from .endpoint import Endpoint, parse as parse_endpoint

DEFAULT_CTX = 16384
#: 1280x1280. Qwen's vision encoder tokenises by pixel count, so this — not the
#: prompt — is what decides whether a 12-megapixel phone photo fits in memory.
DEFAULT_MAX_PIXELS = 1638400
DEFAULT_CONNECT_TIMEOUT = 3.0
#: Vision prefill on a large page can exceed a minute. A short read timeout kills
#: live requests and reads as a flaky endpoint; connect and read are different
#: problems and get different numbers.
DEFAULT_READ_TIMEOUT = 180.0

#: Extraction wants reproducibility, not the model card's prose sampling. The
#: non-thinking recommendation of ``presence_penalty=1.5`` is actively harmful
#: here: JSON keys repeat by design and penalising repeated tokens fights the
#: grammar.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_PRESENCE_PENALTY = 0.0

#: Off for routine extraction: it roughly doubles token count and latency for no
#: accuracy gain on "read this script", and with no reasoning parser configured
#: the trace lands in ``content`` and fails every schema-validated extraction.
DEFAULT_THINKING = False

DEFAULT_AUTH_HEADER = "Authorization"
DEFAULT_AUTH_SCHEME = "Bearer"

#: Cap on prompt size. "Small models degrade badly on long context regardless of
#: the advertised window." A prompt over this is a retrieval bug, never a reason
#: to raise the cap.
MAX_CTX = 32768


@dataclass(frozen=True)
class AuthSettings:
    """Where the key lives and how the far end wants it presented.

    Only ever a *reference*. The config secret scan rejects a live value, and
    ``api_key_env``, ``header`` and ``scheme`` are on its allow list precisely
    because they name a location rather than holding a key.
    """

    api_key_env: str | None = None
    header: str = DEFAULT_AUTH_HEADER
    scheme: str = DEFAULT_AUTH_SCHEME

    def format(self, key: str) -> tuple[str, str]:
        """The header name and value. Never logged, never returned to a caller."""
        return (self.header, f"{self.scheme} {key}".strip() if self.scheme else key)


@dataclass(frozen=True)
class VlmSettings:
    """The vision-language endpoint: where it is, what it runs, how to talk to it."""

    endpoint: Endpoint
    model: str
    ctx: int = DEFAULT_CTX
    max_pixels: int = DEFAULT_MAX_PIXELS
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout_s: float = DEFAULT_READ_TIMEOUT
    temperature: float = DEFAULT_TEMPERATURE
    presence_penalty: float = DEFAULT_PRESENCE_PENALTY
    thinking: bool = DEFAULT_THINKING
    auth: AuthSettings = field(default_factory=AuthSettings)

    @property
    def long_edge(self) -> int:
        """The longest edge a prepared image may have, from ``max_pixels``.

        A square budget: ``max_pixels`` of 1638400 is 1280x1280, so a page in
        either orientation fits without the caller having to reason about which
        way round the photograph was taken.
        """
        return int(self.max_pixels**0.5)


@dataclass(frozen=True)
class AsrSettings:
    """Speech. Phase 6 uses this; it is validated here so a typo fails early."""

    name: str = "faster-whisper-small"
    compute_type: str = "int8"
    sha256: str | None = None


@dataclass(frozen=True)
class ModelSettings:
    vlm: VlmSettings
    asr: AsrSettings = field(default_factory=AsrSettings)


def _table(data: Mapping[str, Any], name: str, path: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}.{name} must be a table, got {type(value).__name__}")
    return dict(value)


def _string(data: Mapping[str, Any], name: str, path: str, default: str | None = None):
    value = data.get(name, default)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _integer(data: Mapping[str, Any], name: str, path: str, default: int, *, low: int, high: int) -> int:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}.{name} must be an integer, got {value!r}")
    if not low <= value <= high:
        raise ConfigError(f"{path}.{name} must be between {low} and {high}, got {value}")
    return value


def _number(data: Mapping[str, Any], name: str, path: str, default: float, *, low: float, high: float) -> float:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}.{name} must be a number, got {value!r}")
    if not low <= float(value) <= high:
        raise ConfigError(f"{path}.{name} must be between {low} and {high}, got {value}")
    return float(value)


def _boolean(data: Mapping[str, Any], name: str, path: str, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{name} must be true or false, got {value!r}")
    return value


def _scheme(auth: Mapping[str, Any]) -> str:
    """``models.vlm.auth.scheme``, where empty is a real answer rather than a typo.

    Every other string in this file is rejected when it is blank, because a
    blank one is a half-finished edit. This one is not: a server that wants
    ``X-API-Key: <key>`` with no word in front of the value is asking for an
    empty scheme, and the only way to say that in a config file is to write
    ``scheme = ""``. Absent still means ``Bearer`` — the default has to stay the
    default for a file that never mentions the key.
    """
    if "scheme" not in auth:
        return DEFAULT_AUTH_SCHEME
    value = auth["scheme"]
    if not isinstance(value, str):
        raise ConfigError(
            f"models.vlm.auth.scheme must be a string, got {value!r}. Write "
            f'scheme = "" for a server that wants the key on its own, with no '
            f"word in front of it."
        )
    return value.strip()


def parse(raw: Mapping[str, Any]) -> ModelSettings:
    """Validate the ``[models]`` tables of an already-loaded config."""
    models = _table(raw, "models", "config.toml")
    vlm = _table(models, "vlm", "models")
    if not vlm:
        raise EndpointNotConfigured(
            "this vault has no inference endpoint configured — config.toml has no "
            "[models.vlm] table. Extraction needs an OpenAI-compatible endpoint on a "
            "machine you control; see MODELS.md."
        )

    model = _string(vlm, "model", "models.vlm")
    if model is None:
        raise ConfigError(
            "models.vlm.model is missing. Copy it verbatim from your server's "
            "/v1/models — it is checked against what the server reports on every "
            "call, so an approximation will stop the run rather than be ignored."
        )

    auth_table = _table(vlm, "auth", "models.vlm")
    auth = AuthSettings(
        api_key_env=_string(auth_table, "api_key_env", "models.vlm.auth"),
        header=_string(auth_table, "header", "models.vlm.auth", DEFAULT_AUTH_HEADER),
        scheme=_scheme(auth_table),
    )

    asr_table = _table(models, "asr", "models")
    asr = AsrSettings(
        name=_string(asr_table, "name", "models.asr", AsrSettings.name) or AsrSettings.name,
        compute_type=_string(asr_table, "compute_type", "models.asr", AsrSettings.compute_type)
        or AsrSettings.compute_type,
        sha256=_string(asr_table, "sha256", "models.asr"),
    )

    return ModelSettings(
        vlm=VlmSettings(
            endpoint=parse_endpoint(vlm.get("base_url")),
            model=model,
            ctx=_integer(vlm, "ctx", "models.vlm", DEFAULT_CTX, low=512, high=MAX_CTX),
            max_pixels=_integer(
                vlm, "max_pixels", "models.vlm", DEFAULT_MAX_PIXELS,
                low=65536, high=4194304,
            ),
            connect_timeout_s=_number(
                vlm, "connect_timeout_s", "models.vlm", DEFAULT_CONNECT_TIMEOUT,
                low=0.1, high=60.0,
            ),
            read_timeout_s=_number(
                vlm, "read_timeout_s", "models.vlm", DEFAULT_READ_TIMEOUT,
                low=1.0, high=1800.0,
            ),
            temperature=_number(
                vlm, "temperature", "models.vlm", DEFAULT_TEMPERATURE, low=0.0, high=2.0
            ),
            presence_penalty=_number(
                vlm, "presence_penalty", "models.vlm", DEFAULT_PRESENCE_PENALTY,
                low=-2.0, high=2.0,
            ),
            thinking=_boolean(vlm, "thinking", "models.vlm", DEFAULT_THINKING),
            auth=auth,
        ),
        asr=asr,
    )


#: Appended to `config.toml` by first-run setup. No key, ever — only the name of
#: the environment variable one might live in.
CONFIG_TEMPLATE = """
[models.vlm]
base_url   = "https://macbook-pro.tailnet.ts.net/v1"   # MagicDNS; resolves to 100.x
model      = "Qwen3.8-Flash-Next-oQ4e-mtp"             # copy verbatim from /v1/models
ctx        = 16384
max_pixels = 1638400                      # 1280x1280
connect_timeout_s = 3
read_timeout_s    = 180
temperature       = 0.0
presence_penalty  = 0.0
thinking          = false

[models.vlm.auth]                         # reference only — never the key itself
api_key_env = "HEALTH_VLM_TOKEN"
header      = "Authorization"
scheme      = "Bearer"

[models.asr]
name         = "faster-whisper-small"
compute_type = "int8"
"""
