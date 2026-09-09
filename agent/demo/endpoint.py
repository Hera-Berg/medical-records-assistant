"""Copying one ``[models.vlm]`` table into a demo vault, and nothing else.

A demo vault carries no inference endpoint by default: it seeds a record, not a
connection to a model, and a placeholder host would make ``extract`` fail with a
DNS error indistinguishable from the user's own box being asleep. But exercising
extraction against the demo then takes a manual step, so ``demo --endpoint-from
<config>`` copies a real one across.

**Exactly two tables, and never the file.** ``[models.vlm]`` and its nested
``[models.vlm.auth]``, rebuilt key by key from the parsed source. Not
``sync_profile`` — a scratch folder is not on anybody's Dropbox and must not
claim to be. Not ``port``, ``locale``, ``[models.asr]``, or any key this project
has not shipped yet. The rebuild is what makes that a guarantee rather than an
intention: a text slice of the source file could carry a trailing line into the
copy, and re-emitting only the scalars found under one table cannot.

**A credential is a hard refusal, not a warning.** ``config.toml`` is scanned for
secrets when it loads, but ``--endpoint-from`` takes any path, and the whole
point of the demo's guardrails is that invented data and real data never mix. A
key anywhere in the source file stops the seeding before a byte is written —
anywhere, not only in the table being copied, because a config holding a
credential is one this project refuses to load at all, and this is the moment
someone is looking at that file.

**The copy names its source, in the folder and not only on the terminal.** The
run that made the vault scrolls away; the vault stays. Someone opening
``config.toml`` in six months has to be able to see that its endpoint was copied,
from where, rather than discover a demo folder talking to a real box.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..config import CONFIG_FILENAME, scan_for_secrets
from ..errors import HealthAgentError

#: The one nested table that travels with ``[models.vlm]``. Everything else
#: under it is dropped and reported — see :func:`copy_from`.
AUTH_TABLE = "auth"


@dataclass(frozen=True)
class CopiedEndpoint:
    """One ``[models.vlm]`` table, lifted from a file that is named."""

    source: Path
    toml: str
    #: Keys under ``[models.vlm]`` that were not copied because they are neither
    #: a scalar nor the ``auth`` table. Reported rather than silently dropped: a
    #: demo endpoint that differs from the real one in a way nobody was told
    #: about is the failure this whole module is arranged to avoid.
    dropped: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"source": str(self.source), "dropped": list(self.dropped)}


def _scalar(value: object) -> str | None:
    """*value* as TOML, or ``None`` if it is not a scalar this copies.

    ``json.dumps`` for strings: TOML basic strings take the same escapes, and
    ``ensure_ascii=False`` leaves any non-ASCII character literal rather than
    emitting the surrogate pairs JSON would and TOML forbids.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return None


def _resolve(path: str | Path) -> Path:
    """The config file *path* names. A directory means the vault root."""
    resolved = Path(path).expanduser()
    if resolved.is_dir():
        resolved = resolved / CONFIG_FILENAME
    if not resolved.is_file():
        raise HealthAgentError(
            f"no config file at {resolved}. --endpoint-from takes the path to a "
            f"{CONFIG_FILENAME}, or to the vault root holding one"
        )
    return resolved


def copy_from(path: str | Path) -> CopiedEndpoint:
    """Read ``[models.vlm]`` out of the config at *path*.

    Validates the result before returning it, so a demo vault is never seeded
    with an endpoint that will fail to load later — at which point the error
    would be about the demo rather than about the file it came from.
    """
    source = _resolve(path)
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HealthAgentError(f"cannot read {source}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise HealthAgentError(f"{source} is not valid TOML: {exc}") from None

    models = data.get("models")
    vlm = models.get("vlm") if isinstance(models, dict) else None
    if not isinstance(vlm, dict) or not vlm:
        raise HealthAgentError(
            f"{source} has no [models.vlm] table, so there is no endpoint to copy. "
            f"Point --endpoint-from at the config of a vault that has one, or leave "
            f"it off — a demo vault carries no endpoint by default."
        )

    # Scanned over the whole document, not only the subtree being copied.
    # Scoping the copy already stops a key reaching the demo vault, but a config
    # holding a credential anywhere is one `agent.config.parse` refuses to load
    # at all — and this is the moment someone is looking at that file. Reading
    # past it in silence would be the one chance to say so, spent.
    leaked = scan_for_secrets(data)
    if leaked:
        in_copy = [name for name in leaked if name.startswith("models.vlm")]
        detail = (
            f"at {', '.join(in_copy)}, in the very table this would copy"
            if in_copy
            else f"at {', '.join(leaked)}, outside the table this would copy"
        )
        raise HealthAgentError(
            f"{source} has what looks like a credential {detail}. Nothing was "
            f"seeded, and nothing was copied. That file lives at a vault root and "
            f"syncs with it, so the value must be considered already disclosed: "
            f"rotate it, take it out of the file, and keep the key in the OS "
            f"keychain or an environment variable with only api_key_env naming it "
            f"here (see MODELS.md, 'Credentials')."
        )

    lines = ["[models.vlm]"]
    dropped: list[str] = []
    auth = vlm.get(AUTH_TABLE)
    for key, value in vlm.items():
        if key == AUTH_TABLE:
            continue
        rendered = _scalar(value)
        if rendered is None:
            dropped.append(f"models.vlm.{key}")
            continue
        lines.append(f"{key} = {rendered}")

    if isinstance(auth, dict) and auth:
        lines.extend(["", "[models.vlm.auth]"])
        for key, value in auth.items():
            rendered = _scalar(value)
            if rendered is None:
                dropped.append(f"models.vlm.auth.{key}")
                continue
            lines.append(f"{key} = {rendered}")
    elif auth is not None and not isinstance(auth, dict):
        dropped.append("models.vlm.auth")

    copied = CopiedEndpoint(
        source=source, toml="\n".join(lines) + "\n", dropped=tuple(dropped)
    )
    _check_loads(copied)
    return copied


def _check_loads(copied: CopiedEndpoint) -> None:
    """Refuse to hand back a table the settings parser will not accept."""
    from ..llm.settings import parse as parse_settings  # noqa: PLC0415 - cycle

    try:
        parse_settings(tomllib.loads(copied.toml))
    except HealthAgentError as exc:
        raise HealthAgentError(
            f"the [models.vlm] table in {copied.source} does not load: {exc}"
        ) from None
