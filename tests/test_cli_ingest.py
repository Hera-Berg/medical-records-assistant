"""``health-agent ingest``, and what ``check`` says about the raw store."""

from __future__ import annotations

import io
import json

from agent.cli import main

from .conftest import JPEG, PDF


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


def test_ingest_stores_a_file_and_reports_where_it_went(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)

    code, output = run("--vault", str(vault.root), "ingest", str(source))
    assert code == 0
    assert "stored" in output
    assert "raw/" in output
    assert len(list((vault.root / "raw").rglob("*.jpg"))) == 1


def test_ingest_takes_several_files_at_once(vault, tmp_path, identity):
    a = tmp_path / "a.jpg"
    a.write_bytes(JPEG)
    b = tmp_path / "b.pdf"
    b.write_bytes(PDF)

    code, output = run("--vault", str(vault.root), "ingest", str(a), str(b))
    assert code == 0
    assert output.count("stored") == 2


def test_a_duplicate_is_reported_as_reseen_and_is_not_a_failure(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    run("--vault", str(vault.root), "ingest", str(source))

    code, output = run("--vault", str(vault.root), "ingest", str(source))
    assert code == 0
    assert "reseen" in output


def test_a_failure_is_reported_without_stopping_the_rest(vault, tmp_path, identity):
    good = tmp_path / "good.jpg"
    good.write_bytes(JPEG)
    missing = tmp_path / "not-here.jpg"

    code, output = run("--vault", str(vault.root), "ingest", str(missing), str(good))
    assert code == 1
    assert "failed" in output
    assert "stored" in output


def test_json_output_carries_the_hash_and_the_event(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)

    code, output = run("--vault", str(vault.root), "ingest", "--json", str(source))
    payload = json.loads(output)
    assert code == 0
    assert payload[0]["status"] == "stored"
    assert len(payload[0]["hash"]) == 64
    assert payload[0]["event"]


def test_the_capture_source_reaches_the_event(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    run("--vault", str(vault.root), "ingest", "--source", "drop", "--note", "fridge", str(source))

    event = next(e for e in vault.read().events if e.type == "artifact.ingested")
    assert event.payload["capture"]["source"] == "drop"
    assert event.payload["capture"]["note"] == "fridge"


def test_an_unknown_capture_source_is_rejected_by_the_parser(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    try:
        run("--vault", str(vault.root), "ingest", "--source", "telepathy", str(source))
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse to reject the source")


# --- check ------------------------------------------------------------------

def test_check_reports_the_raw_store(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    run("--vault", str(vault.root), "ingest", str(source))

    code, output = run("--vault", str(vault.root), "check")
    assert code == 0
    assert "raw" in output
    assert "1 artefacts" in output
    assert "No problems." in output


def test_check_json_includes_the_raw_section(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    run("--vault", str(vault.root), "ingest", str(source))

    _, output = run("--vault", str(vault.root), "check", "--json")
    report = json.loads(output)
    assert report["raw"]["artifacts"] == 1
    assert report["raw"]["deep"] is False


def test_check_deep_re_reads_the_bytes(vault, tmp_path, identity):
    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    run("--vault", str(vault.root), "ingest", str(source))

    _, output = run("--vault", str(vault.root), "check", "--deep", "--json")
    assert json.loads(output)["raw"]["deep"] is True


def test_check_surfaces_an_artefact_that_has_gone_missing(vault, tmp_path, identity):
    import os

    source = tmp_path / "script.jpg"
    source.write_bytes(JPEG)
    _, output = run("--vault", str(vault.root), "ingest", "--json", str(source))
    rel = json.loads(output)[0]["path"]
    os.chmod(vault.root / rel, 0o600)
    (vault.root / rel).unlink()

    code, output = run("--vault", str(vault.root), "check")
    assert code == 1
    assert "is recorded in the event log but is not on disk" in output
