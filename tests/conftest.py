"""Shared fixtures.

Every test runs against a throwaway out-of-vault config home, so no test can
read or write the developer's real device identity or vault pointer.
"""

from __future__ import annotations

import os

import pytest

from agent import config as config_mod
from agent import device as device_mod
from agent import vault as vault_mod
from agent.events import envelope
from agent.vault import Vault

MINIMAL_CONFIG = 'sync_profile = "local"\nport = 7777\nlocale = "en"\n'


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point the out-of-vault config home at tmp and clear every override."""
    home = tmp_path / "config-home"
    monkeypatch.setenv(device_mod.ENV_CONFIG_HOME, str(home))
    for name in (device_mod.ENV_DEVICE, device_mod.ENV_LABEL, vault_mod.ENV_VAULT):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture(autouse=True)
def isolated_reader(tmp_path, monkeypatch):
    """No test may touch this machine's real reader files or start its process.

    The data directory points at tmp, so the reader is "not downloaded" unless a
    test puts files there, and the process-wide reader is replaced before and
    shut down after, so a reader one test started cannot outlive it.
    """
    from agent.runtime import platforms, supervisor

    monkeypatch.setenv(platforms.ENV_DATA_HOME, str(tmp_path / "data-home"))
    previous = supervisor.install(None)
    yield tmp_path / "data-home"
    current = supervisor.install(previous)
    if current is not None:
        current.shutdown()


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch):
    """No test may read or write the developer's own OS keychain.

    The settings screen and the probe both resolve the inference credential,
    which means a plain ``GET /api/settings`` would otherwise reach a real
    Secret Service — slow at best, and on a developer machine it would report
    the state of a key that has nothing to do with the vault under test. Stubbed
    to "no entry"; the tests that exercise the keychain reader itself put the
    real function back.
    """
    from agent.llm import credentials as credentials_mod

    monkeypatch.setattr(credentials_mod, "_from_keychain", lambda: None)


@pytest.fixture(autouse=True)
def restore_writable(tmp_path):
    """Put write permission back on everything under ``tmp_path`` afterwards.

    Stored artefacts are mode ``0o400``. Windows maps that to the read-only
    attribute, which makes ``shutil.rmtree`` refuse, and pytest cleans up older
    ``tmp_path`` directories with exactly that call — so without this the suite
    passes locally on Linux and fails on Windows CI a few runs in, which is a
    miserable thing to debug. Restoring the mode costs nothing and keeps
    teardown boring everywhere.
    """
    yield
    for path in tmp_path.rglob("*"):
        try:
            if path.is_file():
                os.chmod(path, 0o600)
        except OSError:
            pass


#: Minimal but genuine format headers, for the sniffing and ingest paths.
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"1 0 obj\n" * 8
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 64
WEBM = b"\x1aE\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x82\x84webm" + b"\x00" * 64


@pytest.fixture
def identity():
    return device_mod.issue(label="test-machine")


@pytest.fixture
def vault_root(tmp_path):
    root = tmp_path / "health"
    vault_mod.scaffold(root)
    (root / config_mod.CONFIG_FILENAME).write_text(MINIMAL_CONFIG, encoding="utf-8")
    return root


@pytest.fixture
def vault(vault_root, identity):
    return Vault.open(vault_root, identity=identity)


def proposal(device: str, ts: str | None = None, **payload) -> envelope.Event:
    """An agent-authored event, which must carry provenance."""
    return envelope.new(
        "claim.proposed",
        device,
        payload=payload or {"subject": "med:perindopril"},
        provenance={"model": "qwen3.5:9b", "artifact": "a3f91c"},
        ts=ts,
    )


# --- phase 5: the HTTP layer -------------------------------------------------


@pytest.fixture
def app(vault):
    """The application over a scaffolded vault, with the worker switched off.

    ``worker=False`` is passed explicitly rather than relied on. The default is
    already off under pytest — see :func:`agent.server.state.under_pytest` — but
    a test that depends on a default is a test that changes meaning when the
    default does, and a background thread reading artefacts underneath an
    assertion is exactly the kind of flake that takes a day to find.
    """
    from agent.server import create_app

    return create_app(vault, worker=False)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as started:
        yield started


def api_client(vault, **kwargs):
    """A client over *vault* for tests that need non-default app options."""
    from fastapi.testclient import TestClient

    from agent.server import create_app

    kwargs.setdefault("worker", False)
    return TestClient(create_app(vault, **kwargs))


# --- phase 3: hand-authored claims ------------------------------------------
#
# The constructors themselves live in ``agent.demo.authoring``: the demo command
# seeds its vault with the same shapes these tests assert on, and one definition
# of "a well-formed claim payload" is the point. Re-exported here so every test
# keeps importing them from ``.conftest``.

from agent.demo.authoring import (  # noqa: E402
    DEFAULT_ARTIFACT,
    claim,
    confirm,
    correct,
    ingested,
    merge,
    note,
    on_day,
    reject,
)

__all__ = [
    "DEFAULT_ARTIFACT",
    "api_client",
    "claim",
    "confirm",
    "correct",
    "ingested",
    "merge",
    "note",
    "on_day",
    "proposal",
    "reject",
]


# --- phase 10: asking the record --------------------------------------------


class FakeBox:
    """A stand-in for the inference client that records what it was asked.

    The query tests care as much about *what reached the model* as about what
    came back — that a rejected reading is in no byte of the request, that the
    answering call never happens when retrieval found nothing, that exactly one
    expansion round runs — so every call is kept and can be asserted on.

    It speaks the one method :mod:`agent.query` uses and nothing else. A fake
    with a wider surface than the thing it stands in for is a fake that lets
    code under test drift away from the real client unnoticed.
    """

    MODEL = "fake-box/qwen"

    def __init__(self, sentences=None, terms=(), truncated=False, error=None, content=None):
        self.sentences = sentences
        self.terms = tuple(terms)
        self.truncated = truncated
        self.error = error
        self.content = content
        self.closed = False
        self.calls: list[dict] = []

    # -- the surface `agent.query` uses ---------------------------------

    def complete(self, messages, schema=None, schema_name="", max_tokens=0):
        import json

        from agent.llm.client import Completion

        self.calls.append(
            {
                "schema_name": schema_name,
                "messages": [dict(message) for message in messages],
                "max_tokens": max_tokens,
                "text": json.dumps([dict(m) for m in messages], ensure_ascii=False),
            }
        )
        if self.error is not None:
            raise self.error
        if schema_name.endswith("search_terms"):
            body = json.dumps({"terms": list(self.terms)})
        elif self.content is not None:
            body = self.content
        else:
            body = json.dumps({"sentences": list(self.sentences or [])})
        return Completion(
            content=body,
            model=self.MODEL,
            raw={},
            latency_s=0.0,
            finish_reason="length" if self.truncated else "stop",
            sampling={"max_tokens": max_tokens},
        )

    def close(self) -> None:
        """Part of the client's surface: the route closes what it opened."""
        self.closed = True

    # -- what the tests ask it ------------------------------------------

    @property
    def answering_calls(self) -> list[dict]:
        return [call for call in self.calls if not call["schema_name"].endswith("search_terms")]

    @property
    def term_calls(self) -> list[dict]:
        return [call for call in self.calls if call["schema_name"].endswith("search_terms")]

    def everything_sent(self) -> str:
        return "\n".join(call["text"] for call in self.calls)

    def record_sent(self) -> str:
        """Everything sent *except* the question the person typed.

        The distinction matters for the rejected-content tests. A question is
        the user's own words, this second, and sending them to their own box is
        the whole request; the rule is about the *record* never carrying a
        retracted reading back out. Splitting on the prompt's own heading is
        what lets a test assert the strong version of that.
        """
        return "\n".join(call["text"].split("QUESTION")[0] for call in self.calls)


__all__.append("FakeBox")
