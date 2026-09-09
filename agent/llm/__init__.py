"""The inference layer. Nothing outside this package imports a model SDK.

``agent.llm.client`` is the only module that knows an API's wire format; the
rest is about where the endpoint is allowed to be, where the key is allowed to
live, and keeping the key out of everything that gets written down.
"""

from __future__ import annotations

from . import credentials, endpoint, redaction, settings
from .client import Client, Completion

__all__ = [
    "Client",
    "Completion",
    "credentials",
    "endpoint",
    "redaction",
    "settings",
]
