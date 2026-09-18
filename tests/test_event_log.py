"""Appends are pure suffixes, nothing is ever silently dropped."""

from __future__ import annotations

import sys

import pytest

from agent.config import SyncProfile
from agent.errors import AppendError, DeviceIdentityError, EventValidationError
from agent.events import canonical, envelope, log
from agent.events.scan import ShardState

from .conftest import note, proposal

DEVICE = "laptop-a1b2"
LOCAL = SyncProfile.LOCAL


@pytest.fixture
def events_dir(tmp_path):
    path = tmp_path / "events"
    path.mkdir()
    return path


def write_lines(path, *objects):
    path.write_bytes(b"".join(canonical.dump_line(o) for o in objects))


# --- naming -----------------------------------------------------------------

def test_shard_is_one_file_per_device_per_month(events_dir):
    september = log.append(events_dir, note(DEVICE, ts="2026-09-08T14:32:11Z"), LOCAL)
    october = log.append(events_dir, note(DEVICE, ts="2026-10-01T00:00:00Z"), LOCAL)
    other = log.append(events_dir, note("phone-9xyz", ts="2026-09-08T14:32:11Z"), LOCAL)
    assert september.name == "2026-09.laptop-a1b2.jsonl"
    assert october.name == "2026-10.laptop-a1b2.jsonl"
    assert other.name == "2026-09.phone-9xyz.jsonl"


@pytest.mark.parametrize(
    "name",
    [
        "2026-09.laptop-a1b2.jsonl",
        "2026-09.a.jsonl",
    ],
)
def test_valid_shard_names_parse(name):
    assert log.parse_shard_name(name) is not None


@pytest.mark.parametrize(
    "name",
    [
        "2026-09.laptop (conflicted copy 2026-09-08).jsonl",
        "2026-09.laptop(1).jsonl",
        "2026-09.laptop-a1b2 (1).jsonl",
        "2026-13.laptop.jsonl",
        "2026-09.LAPTOP.jsonl",
        "notes.jsonl",
        "2026-09.laptop.json",
    ],
)
def test_forks_and_junk_fail_the_shard_grammar(name):
    # This grammar, not the sync profile, is what keeps a conflicted copy out of
    # the merged view. It works the same under every profile.
    assert log.parse_shard_name(name) is None


# --- append -----------------------------------------------------------------

def test_append_is_a_pure_suffix(events_dir):
    path = log.append(events_dir, note(DEVICE), LOCAL)
    before = path.read_bytes()
    log.append(events_dir, note(DEVICE), LOCAL)
    after = path.read_bytes()
    assert after.startswith(before)
    assert len(after) > len(before)


def test_appended_line_is_canonical_json(events_dir):
    event = note(DEVICE, text="hi")
    path = log.append(events_dir, event, LOCAL)
    assert path.read_bytes() == canonical.dump_line(event.to_dict())


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no POSIX mode: the shard is written and appended to, but its "
    "mode reads back 0666 whatever it was created with.",
)
def test_shard_is_owner_only(events_dir):
    path = log.append(events_dir, note(DEVICE), LOCAL)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "event, match",
    [
        (envelope.Event("nope", "note.recorded", "2026-09-08T14:32:11Z", DEVICE, "user"),
         "not a valid 26-character ULID"),
        (envelope.new("note.invented", DEVICE), "unknown event type"),
        (envelope.new("note.recorded", DEVICE, actor="agent"), "authored by"),
        (envelope.new("note.recorded", DEVICE, ts="2026-09-08"), "canonical UTC"),
        (envelope.new("note.recorded", "Laptop A"), "not a usable device id"),
        (envelope.new("claim.proposed", DEVICE, actor="agent"), "must carry provenance"),
        (envelope.new("note.recorded", DEVICE, provenance={"model": "x"}),
         "must not carry model provenance"),
    ],
)
def test_invalid_envelopes_are_rejected_on_append(events_dir, event, match):
    with pytest.raises(EventValidationError, match=match):
        log.append(events_dir, event, LOCAL)
    assert list(events_dir.iterdir()) == []


def test_agent_events_carry_provenance(events_dir):
    path = log.append(events_dir, proposal(DEVICE), LOCAL)
    event = log.read_all(events_dir, LOCAL).events[0]
    assert event.provenance["model"] == "qwen3.5:9b"
    assert path.exists()


# --- torn final line --------------------------------------------------------

def test_torn_final_line_survives_and_is_reported(events_dir):
    """A crash mid-write must not cost the next event, or the torn bytes."""
    path = log.append(events_dir, note(DEVICE, text="first"), LOCAL)
    intact = path.read_bytes()
    with open(path, "ab") as handle:
        handle.write(b'{"id":"01M1ZZ","type":"note.rec')  # interrupted write

    log.append(events_dir, note(DEVICE, text="third"), LOCAL)

    raw = path.read_bytes()
    assert raw.startswith(intact)
    assert b'{"id":"01M1ZZ","type":"note.rec\n' in raw  # torn bytes kept verbatim

    read = log.read_all(events_dir, LOCAL)
    assert [e.payload["text"] for e in read.events] == ["first", "third"]
    assert len(read.malformed) == 1
    assert "not valid JSON" in read.malformed[0].reason
    assert read.is_clean is False


def test_a_parsable_unterminated_line_is_kept_and_flagged(events_dir):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    event = note(DEVICE, ts="2026-09-08T14:32:11Z", text="tail")
    path.write_bytes(canonical.dumps(event.to_dict()).encode("utf-8"))  # no newline

    read = log.read_all(events_dir, LOCAL)
    assert len(read.events) == 1
    assert any("no terminating newline" in a for a in read.anomalies)


# --- malformed content ------------------------------------------------------

@pytest.mark.parametrize(
    "raw, reason",
    [
        (b"{not json}\n", "not valid JSON"),
        (b'{"id":"x"}\n', "missing required keys"),
        (b'"just a string"\n', "must be a JSON object"),
        (b'{"id":"x","type":"t","ts":"2026-09-08T00:00:00Z","device":"d","actor":"user",'
         b'"payload":[]}\n', "payload must be a JSON object"),
        (b"\xff\xfe not utf8\n", "not valid UTF-8"),
    ],
)
def test_malformed_lines_are_reported_never_dropped(events_dir, raw, reason):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    good = note(DEVICE, ts="2026-09-08T14:32:11Z", text="good")
    path.write_bytes(raw + canonical.dump_line(good.to_dict()))

    read = log.read_all(events_dir, LOCAL)
    assert len(read.events) == 1  # the good line still reads
    assert len(read.malformed) == 1
    assert reason in read.malformed[0].reason
    assert read.malformed[0].lineno == 1
    assert read.is_clean is False


def test_blank_separator_lines_carry_nothing_and_are_skipped(events_dir):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    line = canonical.dump_line(note(DEVICE, ts="2026-09-08T14:32:11Z").to_dict())
    path.write_bytes(line + b"\n\n" + line)
    read = log.read_all(events_dir, LOCAL)
    assert len(read.events) == 2
    assert not read.malformed


def test_unknown_event_type_is_kept_and_flagged(events_dir):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    write_lines(path, {
        "id": "01M1ZZT3EGM5ZMXW9KKX8M6HZG",
        "type": "reading.recorded",  # written by some future phase
        "ts": "2026-09-08T14:32:11Z",
        "device": DEVICE,
        "actor": "user",
        "provenance": None,
        "payload": {},
    })
    read = log.read_all(events_dir, LOCAL)
    assert len(read.events) == 1
    assert any("unknown event type" in a for a in read.anomalies)


def test_unknown_top_level_keys_round_trip_rather_than_vanish(events_dir):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    line = {
        "id": "01M1ZZT3EGM5ZMXW9KKX8M6HZG",
        "type": "note.recorded",
        "ts": "2026-09-08T14:32:11Z",
        "device": DEVICE,
        "actor": "user",
        "provenance": None,
        "payload": {},
        "schema_version": 2,
    }
    write_lines(path, line)
    event = log.read_all(events_dir, LOCAL).events[0]
    assert event.extra == {"schema_version": 2}
    assert canonical.dumps(event.to_dict()) == canonical.dumps(line)


def test_shard_and_event_disagreeing_about_device_is_flagged(events_dir):
    path = events_dir / "2026-09.phone-9xyz.jsonl"
    write_lines(path, note(DEVICE, ts="2026-09-08T14:32:11Z").to_dict())
    read = log.read_all(events_dir, LOCAL)
    assert any("does not match the shard's device" in a for a in read.anomalies)


def test_event_outside_the_shard_month_is_flagged(events_dir):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    write_lines(path, note(DEVICE, ts="2026-11-08T14:32:11Z").to_dict())
    read = log.read_all(events_dir, LOCAL)
    assert any("does not fall in the shard's month" in a for a in read.anomalies)


def test_duplicate_ids_across_shards_are_reported(events_dir):
    event = note(DEVICE, ts="2026-09-08T14:32:11Z")
    write_lines(events_dir / f"2026-09.{DEVICE}.jsonl", event.to_dict())
    # The classic cause: a shard copied from a backup under a new device name.
    write_lines(events_dir / "2026-09.restored-1234.jsonl", event.to_dict())

    read = log.read_all(events_dir, LOCAL)
    assert len(read.duplicate_ids) == 1
    assert read.duplicate_ids[0][0] == event.id
    assert len(read.duplicate_ids[0][1]) == 2
    assert read.is_clean is False


def test_files_that_are_not_shards_are_reported_not_ignored(events_dir):
    (events_dir / "2026-09.laptop (conflicted copy 2026-09-08).jsonl").write_bytes(b"")
    (events_dir / "README.txt").write_text("hello", encoding="utf-8")
    read = log.read_all(events_dir, LOCAL)
    assert len(read.foreign_files) == 2
    assert read.is_clean is False


# --- placeholders -----------------------------------------------------------

def test_empty_shard_is_empty_not_malformed(events_dir):
    (events_dir / f"2026-09.{DEVICE}.jsonl").write_bytes(b"")
    read = log.read_all(events_dir, LOCAL)
    assert read.shards[0].state is ShardState.EMPTY
    assert not read.malformed
    assert read.is_clean


def test_undownloaded_virtual_file_is_a_placeholder_not_corruption(events_dir):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    path.write_bytes(b"")
    path.with_name(path.name + ".nextcloud").write_bytes(b"")

    read = log.read_all(events_dir, SyncProfile.NEXTCLOUD)
    assert read.shards[0].state is ShardState.PLACEHOLDER
    assert "not downloaded" in read.shards[0].detail
    assert not read.malformed
    assert read.unavailable_shards


def test_appending_to_an_undownloaded_shard_is_refused(events_dir):
    # Appending would risk a fork, or a materialised file containing only the
    # new line. Refusing loudly beats losing the earlier events.
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    path.write_bytes(b"")
    path.with_name(path.name + ".nextcloud").write_bytes(b"")

    with pytest.raises(AppendError, match="not been downloaded"):
        log.append(events_dir, note(DEVICE, ts="2026-09-08T14:32:11Z"),
                   SyncProfile.NEXTCLOUD)


def test_a_shard_that_reports_bytes_but_yields_none_is_a_placeholder(events_dir, monkeypatch):
    path = events_dir / f"2026-09.{DEVICE}.jsonl"
    path.write_bytes(b"x" * 100)
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda self: b"")
    monkeypatch.setattr("builtins.open", _empty_open(open))

    read = log.read_all(events_dir, SyncProfile.GDRIVE)
    assert read.shards[0].state is ShardState.PLACEHOLDER
    assert not read.malformed


def _empty_open(real_open):
    class _Empty:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return b""

    def fake(path, mode="r", *args, **kwargs):
        if "b" in mode and str(path).endswith(".jsonl"):
            return _Empty()
        return real_open(path, mode, *args, **kwargs)

    return fake


# --- readback ---------------------------------------------------------------

def test_readback_runs_on_synced_profiles_only(events_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(log, "_verify_readback", lambda *a: calls.append(a))

    log.append(events_dir, note(DEVICE), SyncProfile.LOCAL)
    assert calls == []
    log.append(events_dir, note(DEVICE), SyncProfile.DROPBOX)
    assert len(calls) == 1


def test_readback_failure_is_reported_not_swallowed(events_dir):
    path = log.append(events_dir, note(DEVICE), SyncProfile.DROPBOX)
    with pytest.raises(AppendError, match="read back differently"):
        log._verify_readback(path, b"bytes that were never written\n", 0)


def test_readback_on_a_synced_profile_accepts_a_good_write(events_dir):
    path = log.append(events_dir, note(DEVICE), SyncProfile.DROPBOX)
    log.append(events_dir, note(DEVICE), SyncProfile.DROPBOX)
    assert len(log.read_all(events_dir, SyncProfile.DROPBOX).events) == 2
    assert path.read_bytes().count(b"\n") == 2


# --- vault-level guards -----------------------------------------------------

def test_a_device_only_appends_to_its_own_shard(vault, identity):
    with pytest.raises(DeviceIdentityError, match="own shard"):
        vault.append(note("someone-else"))
    assert vault.append(note(identity.id)).name.endswith(f"{identity.id}.jsonl")
