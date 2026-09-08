"""The phase 1 gate: 10k events across 3 device files replay in deterministic order.

Everything downstream — the projection engine, the byte-identical rebuild, the
whole "the event log is the only source of truth" invariant — assumes that
reading the log twice, or on another machine, or after the files have been
shuffled by a sync client, yields the same sequence. This is that assertion.
"""

from __future__ import annotations

import random
import shutil

import pytest

from agent.config import SyncProfile
from agent.events import canonical, envelope, log

LOCAL = SyncProfile.LOCAL
DEVICES = ("laptop-a1b2", "phone-9xyz", "tablet-k4m7")
MONTHS = ("2026-08", "2026-09")
TOTAL = 10_000


def build_events(seed: int = 7) -> list[envelope.Event]:
    """10k events spread over three devices and two months.

    Timestamps are drawn from a deliberately small pool so that hundreds of
    events share a second. That is the realistic case — a burst of extractions
    lands in the same second — and it is what forces the ULID tiebreak to carry
    the ordering rather than the timestamp alone.
    """
    rng = random.Random(seed)
    events = []
    for i in range(TOTAL):
        device = DEVICES[i % len(DEVICES)]
        month = MONTHS[i % len(MONTHS)]
        day = rng.randint(1, 28)
        hour = rng.randint(0, 23)
        second = rng.randrange(0, 60, 15)
        ts = f"{month}-{day:02d}T{hour:02d}:00:{second:02d}Z"
        if i % 4 == 0:
            events.append(
                envelope.new(
                    "claim.proposed",
                    device,
                    payload={"seq": i, "subject": "med:perindopril"},
                    provenance={"model": "qwen3.5:9b", "artifact": f"{i:06x}"},
                    ts=ts,
                )
            )
        else:
            events.append(envelope.new("note.recorded", device, payload={"seq": i}, ts=ts))
    return events


def write_vault(root, events, *, shuffle_lines=False, seed=1):
    """Lay events out as per-device, per-month shards."""
    root.mkdir(parents=True, exist_ok=True)
    shards: dict[str, list[bytes]] = {}
    for event in events:
        name = log.shard_name(log.month_of(event.ts), event.device)
        shards.setdefault(name, []).append(canonical.dump_line(event.to_dict()))
    rng = random.Random(seed)
    for name, lines in shards.items():
        if shuffle_lines:
            rng.shuffle(lines)
        (root / name).write_bytes(b"".join(lines))
    return shards


@pytest.fixture(scope="module")
def corpus():
    return build_events()


@pytest.fixture(scope="module")
def vault_a(tmp_path_factory, corpus):
    root = tmp_path_factory.mktemp("vault-a") / "events"
    write_vault(root, corpus)
    return root


def test_the_corpus_is_what_the_spec_asks_for(vault_a, corpus):
    assert len(corpus) == TOTAL
    devices = {log.parse_shard_name(p.name)[1] for p in vault_a.iterdir()}
    assert devices == set(DEVICES)


def test_every_event_survives_the_union(vault_a, corpus):
    read = log.read_all(vault_a, LOCAL)
    assert len(read.events) == TOTAL
    assert {e.id for e in read.events} == {e.id for e in corpus}
    assert read.is_clean


def test_reading_twice_gives_the_identical_sequence(vault_a):
    first = [e.id for e in log.read_all(vault_a, LOCAL).events]
    second = [e.id for e in log.read_all(vault_a, LOCAL).events]
    assert first == second


def test_order_is_ts_then_ulid(vault_a):
    events = log.read_all(vault_a, LOCAL).events
    keys = [(e.ts, e.id) for e in events]
    assert keys == sorted(keys)
    # And the tiebreak genuinely does work: many events share a timestamp.
    assert len({ts for ts, _ in keys}) < TOTAL / 2


def test_order_does_not_depend_on_how_lines_sit_in_the_files(tmp_path, vault_a, corpus):
    """A sync client can hand back a file whose lines are in another order."""
    shuffled = tmp_path / "shuffled" / "events"
    write_vault(shuffled, corpus, shuffle_lines=True)
    assert [e.id for e in log.read_all(shuffled, LOCAL).events] == [
        e.id for e in log.read_all(vault_a, LOCAL).events
    ]


def test_order_does_not_depend_on_directory_listing_order(monkeypatch, vault_a):
    expected = [e.id for e in log.read_all(vault_a, LOCAL).events]

    real_iterdir = type(vault_a).iterdir
    monkeypatch.setattr(
        type(vault_a), "iterdir", lambda self: reversed(list(real_iterdir(self)))
    )
    assert [e.id for e in log.read_all(vault_a, LOCAL).events] == expected


def test_order_does_not_depend_on_which_device_wrote_what(tmp_path, vault_a, corpus):
    """Re-shard the same events under different device names.

    Shard membership is a storage detail. The merged sequence is not.
    """
    remapped = tmp_path / "remapped" / "events"
    rotated = {DEVICES[i]: DEVICES[(i + 1) % len(DEVICES)] for i in range(len(DEVICES))}
    moved = [
        envelope.Event(
            id=e.id, type=e.type, ts=e.ts, device=rotated[e.device],
            actor=e.actor, provenance=e.provenance, payload=e.payload,
        )
        for e in corpus
    ]
    write_vault(remapped, moved)
    assert [e.id for e in log.read_all(remapped, LOCAL).events] == [
        e.id for e in log.read_all(vault_a, LOCAL).events
    ]


def test_order_survives_the_vault_being_copied_elsewhere(tmp_path, vault_a):
    """Replay on another machine must produce the same sequence."""
    copy = tmp_path / "another-machine" / "events"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(vault_a, copy)
    assert [e.id for e in log.read_all(copy, LOCAL).events] == [
        e.id for e in log.read_all(vault_a, LOCAL).events
    ]


def test_serialising_the_merged_stream_is_byte_identical_across_reads(vault_a):
    """The property phase 3's rebuild test will lean on."""
    def render():
        return b"".join(
            canonical.dump_line(e.to_dict()) for e in log.read_all(vault_a, LOCAL).events
        )

    assert render() == render()


def test_a_sync_fork_does_not_join_the_merged_view(tmp_path, vault_a, corpus):
    """A conflicted copy must not double every event it contains."""
    forked = tmp_path / "forked" / "events"
    write_vault(forked, corpus)
    source = next(iter(forked.iterdir()))
    shutil.copy(source, forked / f"{source.stem} (conflicted copy 2026-09-08).jsonl")

    read = log.read_all(forked, LOCAL)
    assert len(read.events) == TOTAL
    assert not read.duplicate_ids
    assert len(read.foreign_files) == 1  # surfaced, not ignored
    assert read.is_clean is False


def test_appends_interleaved_across_devices_replay_in_the_same_order(tmp_path):
    """The live path, not a synthetic layout: real appends in a random order."""
    events_dir = tmp_path / "live" / "events"
    rng = random.Random(11)
    minted = []
    for i in range(600):
        device = DEVICES[rng.randrange(len(DEVICES))]
        event = envelope.new("note.recorded", device, payload={"seq": i})
        log.append(events_dir, event, LOCAL)
        minted.append(event)

    read = log.read_all(events_dir, LOCAL)
    assert len(read.events) == 600
    assert read.is_clean
    assert [e.id for e in read.events] == sorted(e.id for e in minted)
