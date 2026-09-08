"""The raw filename grammar: what it produces, and what it refuses."""

from __future__ import annotations

import pytest

from agent.errors import IngestError
from agent.ingest import naming
from agent.ingest.sidecar import path_for
from pathlib import Path

DIGEST = "a3f91c" + "0123456789abcdef" * 3 + "0123456789"


# --- building ---------------------------------------------------------------

def test_filename_matches_the_form_in_the_spec():
    assert naming.build("2026-09-08T14:32:11Z", "a3f91c", "jpg") == "2026-09-08T1432Z_a3f91c.jpg"


def test_month_directory_comes_from_the_ingest_timestamp():
    assert str(naming.month_dir("2026-09-08T14:32:11Z")) == "raw/2026/09"
    assert str(naming.month_dir("2027-01-01T00:00:00Z")) == "raw/2027/01"


def test_timestamps_are_normalised_to_utc():
    # The log writes canonical UTC, but a hand-written or imported timestamp
    # with an offset must not land in a different month than it belongs to.
    assert naming.filename_ts("2026-09-01T09:30:00+10:00") == "2026-08-31T2330Z"
    assert str(naming.month_dir("2026-09-01T09:30:00+10:00")) == "raw/2026/08"


def test_unparseable_timestamp_is_refused():
    with pytest.raises(IngestError):
        naming.build("not a timestamp", "a3f91c", "jpg")


def test_bad_extension_is_refused():
    for ext in ("", "JPG", "j p g", "verylongext", "jp/g"):
        with pytest.raises(IngestError):
            naming.build("2026-09-08T14:32:11Z", "a3f91c", ext)


# --- collisions -------------------------------------------------------------

def test_allocate_uses_the_short_hash_when_free():
    name = naming.allocate("2026-09-08T14:32:11Z", DIGEST, "jpg", taken=lambda stem: False)
    assert name == f"2026-09-08T1432Z_{DIGEST[:6]}.jpg"


def test_allocate_lengthens_the_prefix_rather_than_overwriting():
    """Six hex characters collide at around four thousand artefacts.

    A lifetime record passes that, so the allocator must widen rather than
    return a name that is already in use.
    """
    used = {f"2026-09-08T1432Z_{DIGEST[:6]}"}
    name = naming.allocate("2026-09-08T14:32:11Z", DIGEST, "jpg", taken=used.__contains__)
    assert name == f"2026-09-08T1432Z_{DIGEST[:7]}.jpg"


def test_allocate_keeps_widening_while_names_are_taken():
    used = {f"2026-09-08T1432Z_{DIGEST[:n]}" for n in (6, 7, 8, 9)}
    name = naming.allocate("2026-09-08T14:32:11Z", DIGEST, "jpg", taken=used.__contains__)
    assert name == f"2026-09-08T1432Z_{DIGEST[:10]}.jpg"


def test_allocated_names_do_not_depend_on_arrival_order():
    """The name is derived from content, never from a counter."""
    first = naming.allocate("2026-09-08T14:32:11Z", DIGEST, "jpg", taken=lambda stem: False)
    second = naming.allocate("2026-09-08T14:32:11Z", DIGEST, "jpg", taken=lambda stem: False)
    assert first == second


def test_allocate_refuses_when_the_whole_digest_is_taken():
    # Identical content should have been deduplicated long before here.
    with pytest.raises(IngestError):
        naming.allocate("2026-09-08T14:32:11Z", DIGEST, "jpg", taken=lambda stem: True)


# --- parsing ----------------------------------------------------------------

def test_parse_round_trips_a_built_name():
    name = naming.build("2026-09-08T14:32:11Z", "a3f91c", "jpg")
    parsed = naming.parse(name)
    assert parsed is not None
    assert (parsed.ts, parsed.short, parsed.ext) == ("2026-09-08T1432Z", "a3f91c", "jpg")
    assert parsed.name == name


@pytest.mark.parametrize(
    "name",
    [
        "2026-09-08T1432Z_a3f91c.jpg.json",   # a sidecar, not an artefact
        "2026-09-08T143211Z_a3f91c.jpg",      # seconds precision
        "2026-09-08T1432Z_A3F91C.jpg",        # uppercase digest
        "2026-09-08T1432Z_a3f91.jpg",         # digest prefix too short
        "2026-09-08T1432Z_a3f91z.jpg",        # not hex
        "2026-09-08T1432_a3f91c.jpg",         # no zone marker
        "2026-13-08T1432Z_a3f91c.jpg",        # not a real month
        "a3f91c.jpg",
        "IMG_4821.jpg",
        "2026-09-08T1432Z_a3f91c",            # no extension
    ],
)
def test_parse_rejects_anything_else(name):
    assert naming.parse(name) is None


def test_sidecar_name_never_parses_as_an_artefact():
    """Otherwise a directory scan counts sidecars as artefacts.

    Appending ``.json`` to the whole filename rather than replacing the
    extension also keeps a JSON artefact from colliding with its own sidecar.
    """
    artifact = Path("2026-09-08T1432Z_a3f91c.json")
    assert naming.parse(artifact.name) is not None
    assert naming.parse(path_for(artifact).name) is None
    assert path_for(artifact).name == "2026-09-08T1432Z_a3f91c.json.json"


def test_partial_files_are_recognisable():
    assert naming.is_partial(".ingest-01ABC.partial")
    assert not naming.is_partial("2026-09-08T1432Z_a3f91c.jpg")
