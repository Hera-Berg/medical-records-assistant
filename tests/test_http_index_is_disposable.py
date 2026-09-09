"""``.agent/index.sqlite`` is a cache, and deleting it is unobservable.

``CLAUDE.md`` calls the index "a disposable cache … Never the only home of any
fact." That is a claim about behaviour, so it is tested as one: every response
is captured with the index present, the file is deleted, and the same responses
are compared byte for byte.

The design that makes this hold is that the index only ever *accelerates* a
question the projection can also answer. Every query returns ``None`` to mean "I
cannot answer this", and every caller reads that as "ask the projection". A
query that could only be answered from SQLite would be a fact living in a cache,
which is the thing the invariant forbids.

Also tested here: the index is excluded from byte-identical rebuild. SQLite
files carry page-level state that differs between two runs producing identical
logical content, so comparing them would fail for reasons that say nothing about
the record. Excluding it narrows nothing, because the same test deletes it and
requires the wiki bytes to match anyway.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from agent import projection, vault as vault_mod
from agent.server.index import INDEX_FILENAME, Index
from agent.vault import Vault

from .conftest import claim, confirm, correct, ingested, note, on_day

AS_OF = "2026-09-30T00:00:00Z"

ROUTES = (
    "/api/wiki",
    "/api/timeline",
    "/api/timeline?from=2026-06-01&to=2026-09-30",
    "/api/timeline?subject=med:perindopril",
    "/api/timeline?tier=prescriber-issued",
    "/api/timeline/months",
    "/api/medications",
    "/api/artifacts",
    "/api/review",
    "/api/wiki/med:perindopril",
    "/api/artifact/a3f91c/meta",
)


@pytest.fixture
def record(vault):
    device = vault.identity.id
    events = [
        ingested(device, "a3f91c", ts=on_day(1), artifact_ts="2026-06-04T00:00:00Z"),
        ingested(device, "77b210", ts=on_day(2), mime="application/pdf"),
        note(device, ts=on_day(2, hour=18), text="Headaches started around Easter."),
    ]
    dose = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(3), artifact="a3f91c",
        occurred={"value": "2026-06-04", "precision": "day"},
    )
    events += [dose, confirm(device, dose.id, ts=on_day(3, hour=11))]
    misread = claim(
        device, "med:atorvastatin", "dose", "2mg daily", ts=on_day(4), artifact="77b210",
    )
    events += [
        misread,
        correct(device, value="20mg daily", target=misread.id, ts=on_day(4, hour=12)),
    ]
    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    return vault


def _capture_all(client) -> dict[str, tuple[int, str]]:
    return {route: (r.status_code, r.text) for route in ROUTES for r in [client.get(route)]}


def test_deleting_the_index_changes_no_response(record, client):
    """The whole point of the file, stated as an assertion."""
    warm = _capture_all(client)
    index_path = record.root / ".agent" / INDEX_FILENAME
    assert index_path.is_file(), "nothing built the index"

    index_path.unlink()
    cold = _capture_all(client)
    assert cold == warm

    # And it comes back on its own, without anyone asking for a rebuild.
    assert index_path.is_file()
    assert _capture_all(client) == warm


def test_a_corrupt_index_is_a_miss_not_an_error(record, client):
    """A cache that cannot be read must not take a request down."""
    warm = _capture_all(client)
    index_path = record.root / ".agent" / INDEX_FILENAME
    index_path.write_bytes(b"this is not a database, it is a sync client's mistake")

    assert _capture_all(client) == warm


def test_an_index_from_an_older_schema_is_discarded_not_migrated(record, client):
    """A migration path for a cache is code that can only introduce bugs."""
    warm = _capture_all(client)
    index_path = record.root / ".agent" / INDEX_FILENAME

    with sqlite3.connect(index_path) as connection:
        connection.execute("UPDATE meta SET value = '0' WHERE key = 'schema'")

    assert _capture_all(client) == warm
    with sqlite3.connect(index_path) as connection:
        schema = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema'"
        ).fetchone()[0]
    assert schema != "0"


def test_an_index_that_cannot_be_written_is_not_a_failure(record, client, monkeypatch):
    """A read-only vault still serves. It just serves without the cache."""
    monkeypatch.setattr(
        Index, "_connect", lambda self: None
    )
    body = client.get("/api/timeline?from=2026-06-01").json()
    assert body["rows"], "the projection did not answer when the cache could not"


def test_a_stale_index_is_refreshed_when_the_log_moves(record, client):
    """The generation is the log's fingerprint, so an append invalidates it."""
    before = client.get("/api/timeline").json()["total"]

    device = record.identity.id
    record.append(ingested(device, "cc9012", ts=on_day(9), mime="audio/webm"))

    after = client.get("/api/timeline").json()["total"]
    assert after == before + 1


def test_the_index_is_excluded_from_byte_identical_rebuild(record):
    """It holds no fact the wiki does not, and its bytes are not reproducible.

    The exclusion is named in one place — ``agent.vault.NON_DETERMINISTIC_DERIVED``
    — rather than listed in a test, so the reason travels with the rule.
    """
    assert ".agent/index.sqlite" in vault_mod.NON_DETERMINISTIC_DERIVED

    vault = Vault.open(record.root, identity=record.identity)
    projection.rebuild(vault, as_of=AS_OF)
    Index.open(record.root / ".agent").refresh(
        _snapshot_of(record)
    )
    before = _derived(record.root)
    assert before, "the rebuild produced no derived files"

    for name in ("wiki", ".agent"):
        shutil.rmtree(record.root / name, ignore_errors=True)
    vault_mod.scaffold(record.root)

    projection.rebuild(Vault.open(record.root, identity=record.identity), as_of=AS_OF)
    assert _derived(record.root) == before


def test_every_excluded_path_is_genuinely_disposable(record, client):
    """Deleting all of them loses no fact, only work in progress.

    That is what earns them the exclusion. A path excluded from the comparison
    because reproducing it is inconvenient would be a hole in invariant 1; a
    path excluded because it holds nothing is not.
    """
    client.get("/api/timeline")
    vault = Vault.open(record.root, identity=record.identity)
    projection.rebuild(vault, as_of=AS_OF)
    before = _derived(record.root)

    for rel in vault_mod.NON_DETERMINISTIC_DERIVED:
        target = record.root / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)

    projection.rebuild(Vault.open(record.root, identity=record.identity), as_of=AS_OF)
    assert _derived(record.root) == before


def _derived(root) -> dict[str, bytes]:
    """Every derived byte, minus the caches of work in progress."""
    found: dict[str, bytes] = {}
    for name in ("wiki", ".agent"):
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if vault_mod.is_non_deterministic(rel):
                continue
            found[rel] = path.read_bytes()
    return found


def _snapshot_of(vault):
    from agent.server.state import RecordState

    return RecordState(vault).snapshot()
