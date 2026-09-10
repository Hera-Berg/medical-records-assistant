"""`health-agent check` — the only hand-operable surface until phase 5."""

from __future__ import annotations

import io
import json

import pytest

from agent import device as device_mod
from agent import vault as vault_mod
from agent.cli import EXIT_OK, EXIT_PROBLEMS, main
from agent.config import CONFIG_FILENAME

from .conftest import MINIMAL_CONFIG, note


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


def run_json(*argv):
    code, text = run(*argv, "--json")
    return code, json.loads(text)


def test_clean_vault_reports_no_problems(vault_root, identity):
    code, text = run("--vault", str(vault_root), "check")
    assert code == EXIT_OK
    assert "No problems." in text
    assert identity.id in text


def test_check_writes_nothing_without_fix(tmp_path):
    root = tmp_path / "health"
    root.mkdir()
    code, text = run("--vault", str(root), "check")
    assert code == EXIT_PROBLEMS
    assert list(root.iterdir()) == []  # not even a write probe
    assert not device_mod.identity_path().exists()


def test_missing_config_prints_a_template_to_copy(tmp_path):
    root = tmp_path / "health"
    root.mkdir()
    code, text = run("--vault", str(root), "check")
    assert code == EXIT_PROBLEMS
    assert 'sync_profile = "local"' in text
    assert "port = 7777" in text


def test_fix_scaffolds_and_issues_an_identity(tmp_path):
    root = tmp_path / "health"
    root.mkdir()
    (root / CONFIG_FILENAME).write_text(MINIMAL_CONFIG, encoding="utf-8")

    code, report = run_json("--vault", str(root), "check", "--fix")
    assert code == EXIT_OK
    assert set(report["vault"]["created"]) == set(vault_mod.VAULT_DIRS)
    assert report["device"]["issued"] is True
    assert device_mod.identity_path().exists()
    for name in vault_mod.VAULT_DIRS:
        assert (root / name).is_dir()


def test_fix_never_writes_config_toml(tmp_path):
    root = tmp_path / "health"
    root.mkdir()
    code, text = run("--vault", str(root), "check", "--fix")
    assert code == EXIT_PROBLEMS
    assert not (root / CONFIG_FILENAME).exists()


def test_fix_is_idempotent(vault_root, identity):
    first, _ = run("--vault", str(vault_root), "check", "--fix")
    code, report = run_json("--vault", str(vault_root), "check", "--fix")
    assert first == code == EXIT_OK
    assert report["vault"]["created"] == []


def test_fix_records_the_vault_pointer_so_later_runs_need_no_flag(tmp_path):
    root = tmp_path / "health"
    root.mkdir()
    (root / CONFIG_FILENAME).write_text(MINIMAL_CONFIG, encoding="utf-8")
    run("--vault", str(root), "check", "--fix")

    code, report = run_json("check")
    assert code == EXIT_OK
    assert report["vault"]["source"] == "pointer"
    assert report["vault"]["root"] == str(root)


def test_env_var_locates_the_vault(vault_root, identity, monkeypatch):
    monkeypatch.setenv(vault_mod.ENV_VAULT, str(vault_root))
    code, report = run_json("check")
    assert code == EXIT_OK
    assert report["vault"]["source"] == "env"


def test_with_nowhere_to_look_it_says_so(tmp_path):
    code, report = run_json("check")
    assert code == EXIT_PROBLEMS
    assert report["vault"]["status"] == "unresolved"
    assert "HEALTH_VAULT" in report["vault"]["error"]


def test_a_secret_in_config_fails_the_check(vault_root, identity):
    (vault_root / CONFIG_FILENAME).write_text(
        MINIMAL_CONFIG + '\n[models.vlm.auth]\napi_key = "sk-live-abcdef"\n',
        encoding="utf-8",
    )
    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_PROBLEMS
    assert report["config"]["status"] == "invalid"
    assert "models.vlm.auth.api_key" in report["config"]["error"]


def test_the_key_value_itself_is_not_echoed_back(vault_root, identity):
    (vault_root / CONFIG_FILENAME).write_text(
        MINIMAL_CONFIG + '\n[models.vlm.auth]\napi_key = "sk-live-abcdef"\n',
        encoding="utf-8",
    )
    code, text = run("--vault", str(vault_root), "check")
    assert "sk-live-abcdef" not in text


def test_events_are_counted_and_shards_listed(vault, identity, vault_root):
    vault.append(note(identity.id, ts="2026-08-01T00:00:00Z"))
    vault.append(note(identity.id, ts="2026-09-08T14:32:11Z"))

    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_OK
    assert report["log"]["event_count"] == 2
    assert report["log"]["first_ts"] == "2026-08-01T00:00:00Z"
    assert report["log"]["last_ts"] == "2026-09-08T14:32:11Z"
    assert {s["name"] for s in report["log"]["shards"]} == {
        f"2026-08.{identity.id}.jsonl",
        f"2026-09.{identity.id}.jsonl",
    }


def test_a_conflicted_copy_is_surfaced(vault_root, identity):
    fork = vault_root / "events" / "2026-09.laptop (conflicted copy 2026-09-08).jsonl"
    fork.write_bytes(b"")
    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_PROBLEMS
    assert report["conflicts"] == [str(fork.relative_to(vault_root))]


def test_a_fork_is_reported_once_not_twice(vault_root, identity):
    """It fails the shard grammar *and* matches a fork pattern. Say it once."""
    fork = vault_root / "events" / "2026-09.laptop (conflicted copy 2026-09-08).jsonl"
    fork.write_bytes(b"")
    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_PROBLEMS
    assert len([p for p in report["problems"] if fork.name in p]) == 1


def test_junk_in_events_is_still_reported_on_its_own(vault_root, identity):
    (vault_root / "events" / "notes.txt").write_text("hi", encoding="utf-8")
    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_PROBLEMS
    assert any("not a valid shard filename" in p for p in report["problems"])


def test_a_malformed_line_is_surfaced(vault_root, identity):
    shard = vault_root / "events" / f"2026-09.{identity.id}.jsonl"
    shard.write_bytes(b"{ this is not json }\n")
    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_PROBLEMS
    assert len(report["log"]["malformed"]) == 1


def test_a_cloned_identity_is_surfaced(vault_root, identity):
    data = json.loads(identity.path.read_text(encoding="utf-8"))
    data["hostname"] = "someone-elses-imac"
    identity.path.write_text(json.dumps(data), encoding="utf-8")

    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_PROBLEMS
    assert report["device"]["status"] == "mismatch"


def test_a_synced_profile_warns_about_shared_storage(vault_root, identity):
    (vault_root / CONFIG_FILENAME).write_text(
        'sync_profile = "dropbox"\n', encoding="utf-8"
    )
    code, report = run_json("--vault", str(vault_root), "check")
    assert code == EXIT_OK
    # The substance, not the wording: the account reads everything, and a share
    # cannot be taken back. Phrased for the person reading it, not for a log.
    assert any("cannot reliably be taken back" in note for note in report["notes"])
    assert any("Dropbox" in note for note in report["notes"])


def test_a_command_is_required():
    with pytest.raises(SystemExit):
        main([])


def test_serve_says_where_it_is_before_it_blocks(vault, monkeypatch, capsys):
    """`health-agent serve` must announce itself in a pipe, not only a terminal.

    Found by running it under `nohup`: the log was empty. Python line-buffers a
    terminal and block-buffers a pipe, and uvicorn never returns the process, so
    every line above the bind was invisible whenever the output was redirected —
    which is how a service manager runs it, and exactly when a person most needs
    to be told which vault and which port.
    """
    import io

    from agent import cli
    from agent.server import runtime

    bound: dict[str, object] = {}

    def fake_serve(vault_arg, host, port, worker):
        bound.update(host=host, port=port, worker=worker)

    monkeypatch.setattr(runtime, "serve", fake_serve)
    monkeypatch.setattr("agent.server.serve", fake_serve, raising=False)

    stream = io.StringIO()
    flushed: list[bool] = []
    original_flush = stream.flush
    stream.flush = lambda: (flushed.append(True), original_flush())[1]

    code = cli.main(["serve", "--vault", str(vault.root), "--port", "7799"], out=stream)

    assert code == 0
    assert flushed, "nothing was flushed before the process was handed to uvicorn"
    printed = stream.getvalue()
    assert str(vault.root) in printed
    assert "http://127.0.0.1:7799" in printed
    assert bound == {"host": "127.0.0.1", "port": 7799, "worker": True}


def test_serve_refuses_a_host_other_machines_can_reach(vault, capsys):
    """The port is the only boundary; widening it publishes the whole record."""
    import io

    from agent import cli

    stream = io.StringIO()
    code = cli.main(
        ["serve", "--vault", str(vault.root), "--host", "0.0.0.0"], out=stream
    )
    assert code == 1
    assert "no authentication" in stream.getvalue()
