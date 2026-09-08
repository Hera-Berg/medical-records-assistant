"""``health-agent rebuild``: the only way to exercise the projection by hand.

It appends nothing. A rebuild reads ``events/``, writes ``wiki/`` and the
manifest, and leaves the log and the raw store untouched — so running it twice,
or running it on someone else's vault, can lose nothing.
"""

from __future__ import annotations

import io
import json

from agent import cli

from .conftest import claim, confirm, ingested, on_day


def _populate(vault, identity):
    proposed = claim(
        identity.id, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        occurred={"value": "2026-06-04"},
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
    )
    pending = claim(identity.id, "allergy:penicillin", "reaction", "rash", ts=on_day(4))
    for event in sorted(
        [ingested(identity.id), proposed, confirm(identity.id, proposed.id, ts=on_day(3)), pending],
        key=lambda event: event.sort_key,
    ):
        vault.append(event)


def _run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(list(argv), out=out)
    return code, out.getvalue()


def test_rebuild_writes_the_wiki_and_reports_the_queue(vault, vault_root, identity):
    _populate(vault, identity)

    code, output = _run("--vault", str(vault_root), "rebuild", "--as-of", "2026-09-30T00:00:00Z")

    assert code == cli.EXIT_OK
    assert "1 entities" in output
    assert "1 high" in output
    assert "waiting for you to confirm it" in output
    assert (vault_root / "wiki" / "medications" / "perindopril.md").exists()


def test_rebuild_json_is_machine_readable(vault, vault_root, identity):
    _populate(vault, identity)

    code, output = _run(
        "--vault", str(vault_root), "rebuild", "--as-of", "2026-09-30T00:00:00Z", "--json"
    )
    report = json.loads(output)

    assert code == cli.EXIT_OK
    assert report["as_of"] == "2026-09-30T00:00:00Z"
    assert report["stats"]["entities"] == 1
    assert report["current_medications"][0]["id"] == "med:perindopril"
    assert report["review"][0]["consequence"] == "high"


def test_rebuild_appends_nothing_to_the_log(vault, vault_root, identity):
    _populate(vault, identity)
    before = {
        path.name: path.read_bytes() for path in sorted((vault_root / "events").glob("*.jsonl"))
    }

    _run("--vault", str(vault_root), "rebuild", "--as-of", "2026-09-30T00:00:00Z")

    after = {
        path.name: path.read_bytes() for path in sorted((vault_root / "events").glob("*.jsonl"))
    }
    assert after == before


def test_as_of_is_what_makes_two_rebuilds_agree(vault, vault_root, identity):
    _populate(vault, identity)

    _run("--vault", str(vault_root), "rebuild", "--as-of", "2026-06-10T00:00:00Z")
    fresh = (vault_root / "wiki" / "medications" / "perindopril.md").read_bytes()

    _run("--vault", str(vault_root), "rebuild", "--as-of", "2027-06-10T00:00:00Z")
    later = (vault_root / "wiki" / "medications" / "perindopril.md").read_bytes()

    assert b"status: active" in fresh
    assert b"status: stale" in later, "staleness is a function of as_of, not of the log"

    _run("--vault", str(vault_root), "rebuild", "--as-of", "2026-06-10T00:00:00Z")
    assert (vault_root / "wiki" / "medications" / "perindopril.md").read_bytes() == fresh


def test_a_bad_as_of_is_refused_rather_than_guessed(vault, vault_root, identity):
    _populate(vault, identity)
    try:
        _run("--vault", str(vault_root), "rebuild", "--as-of", "last Tuesday")
    except ValueError as exc:
        assert "not a timestamp" in str(exc)
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("an unparseable --as-of should not be accepted")
