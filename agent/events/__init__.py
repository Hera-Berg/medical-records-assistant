"""The event log: the only source of truth in this system.

Everything under ``wiki/`` and ``.agent/`` is derived and disposable. These
modules own ULID generation, canonical serialisation, envelope validation, and
the append/union-read paths over per-device JSONL shards.
"""
