"""Opening a configured connection to the box, from a vault.

One place that knows how to get from "a vault the user nominated" to "a client
that can be asked to read a page", so the CLI does not assemble it and neither
will the HTTP layer in phase 5.

The settings come from ``config.toml``'s ``[models]`` tables and the credential
does not: the config holds a *reference* to where the key lives, and the key
itself is resolved per call from the environment, the keychain or a file outside
the vault. That split is why this function can be called with a vault that syncs
to Dropbox without anything being disclosed.
"""

from __future__ import annotations

from ..config import CONFIG_FILENAME
from ..errors import EndpointNotConfigured
from ..llm import redaction
from ..llm.client import Client
from ..llm.settings import ModelSettings, parse as parse_settings


def settings_for(vault) -> ModelSettings:
    """The validated ``[models]`` tables of this vault's config.

    A vault with no endpoint at all gets the message re-raised with what this
    layer knows and :mod:`agent.llm.settings` does not: *which* vault, and
    whether it is a demo. A demo vault deliberately carries no endpoint, and
    without that sentence its absence reads as something the user broke.
    """
    try:
        return parse_settings(vault.config.raw)
    except EndpointNotConfigured as exc:
        if vault.is_demo:
            raise EndpointNotConfigured(
                f"{vault.root} has no inference endpoint configured — demo vaults "
                f"don't carry one. A demo seeds a record, not a connection to a "
                f"model. Point --vault at your real vault to extract, or paste a "
                f"[models.vlm] table into {vault.root / CONFIG_FILENAME} (see "
                f"MODELS.md)."
            ) from None
        raise EndpointNotConfigured(
            f"{vault.root / CONFIG_FILENAME} has no [models.vlm] table, so this "
            f"vault has no inference endpoint configured. Extraction needs an "
            f"OpenAI-compatible endpoint on a machine you control; see MODELS.md."
        ) from exc


def open_client(vault, transport=None, resolver=None) -> Client:
    """A client for this vault's configured endpoint.

    Does not contact the box. The address guard runs on the first call and on
    every call after it, so constructing this is safe even with the box asleep —
    which matters, because the CLI builds one before deciding what to do.
    """
    redaction.install()
    return Client(
        settings_for(vault).vlm,
        vault_root=vault.root,
        transport=transport,
        resolver=resolver,
    )
