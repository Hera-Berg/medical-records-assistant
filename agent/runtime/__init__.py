"""The reader on this computer: its files, its download, and its process.

``agent/llm/`` does not know this package exists. The client speaks OpenAI HTTP
to a base URL, the address guard already permits loopback, and so a model on
this machine is a ``llama-server`` process at ``http://127.0.0.1:{port}/v1``
that the client is handed settings for. Everything that makes that process
trustworthy lives here instead:

- :mod:`.platforms` — which build this machine needs, where files live outside
  the vault, and how much memory and disk there is.
- :mod:`.manifest` — every file, pinned by URL at a fixed revision, exact size
  and sha256. The pins are code, not configuration: a pin a person can edit to
  anything defeats the check it exists for.
- :mod:`.store` — what is on disk and whether it is verified.
- :mod:`.download` — the one-time fetch, which is the only outbound connection
  this app makes to anything but a machine the user owns.
- :mod:`.choice` — which reader this machine uses, stored beside the device
  identity and never in the synced ``config.toml``.
- :mod:`.supervisor` — starting, checking, sleeping and stopping the process.
- :mod:`.states` — every sentence any of the above may put on a screen.
"""
