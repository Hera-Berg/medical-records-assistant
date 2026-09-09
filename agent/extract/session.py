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

from ..llm import redaction
from ..llm.client import Client
from ..llm.settings import ModelSettings, parse as parse_settings


def settings_for(vault) -> ModelSettings:
    """The validated ``[models]`` tables of this vault's config."""
    return parse_settings(vault.config.raw)


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
