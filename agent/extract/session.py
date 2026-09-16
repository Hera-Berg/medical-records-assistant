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
from ..runtime import choice as choice_mod
from ..runtime import supervisor


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


def reads_here(vault) -> bool:
    """Whether this machine reads this vault's documents itself.

    A demo vault is no exception. The reader's files live outside every vault, so
    reading a demo on this computer downloads nothing a real vault would not
    use; a demo can already reach a remote box, so refusing only this path would
    draw a line that tracks nothing; and invented documents read by a local model
    are the one way to watch extraction work end to end with no personal document
    anywhere. What guards a download is the size confirmation every vault gets.
    """
    return choice_mod.load(vault).reads_here


def open_client(vault, transport=None, resolver=None, wake: bool = True) -> Client:
    """A client for whichever computer reads this vault's documents on this machine.

    **Another computer**: the configured endpoint. Does not contact the box. The
    address guard runs on the first call and on every call after it, so
    constructing this is safe even with the box asleep — which matters, because
    the CLI builds one before deciding what to do.

    **This computer**: the reader is started if it is asleep and *wake* is true,
    and the client is handed the reader's own key through an explicit
    credential. Never through the resolver chain — see
    :class:`agent.llm.client.Client`. A reader that cannot run raises
    :class:`agent.errors.ReaderUnavailable`, which is an
    :class:`~agent.errors.EndpointUnreachable`: everything that already waits
    for a sleeping box waits for it the same way.
    """
    redaction.install()
    if not reads_here(vault):
        return Client(
            settings_for(vault).vlm,
            vault_root=vault.root,
            transport=transport,
            resolver=resolver,
        )

    reader = supervisor.get(vault)
    if wake:
        reader.ensure_ready()
    settings = reader.settings()
    port = settings.endpoint.port
    return Client(
        settings,
        vault_root=vault.root,
        transport=transport,
        resolver=resolver,
        credential=lambda: reader.credential_for(port),
        runtime=reader.runtime_facts(),
    )
