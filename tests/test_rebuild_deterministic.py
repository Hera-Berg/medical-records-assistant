"""Phase 3 gate: delete the derived files, replay, get the same bytes back.

Invariant 1 says the event log is the only source of truth and everything under
``wiki/`` and ``.agent/`` is derived from it. That claim is only worth anything
if it is mechanically true, so this deletes both and rebuilds from ``raw/`` plus
``events/`` alone.

Byte-identity is asserted against three separate ways the environment could leak
into the output:

* **the clock** — pinned by passing ``as_of`` explicitly, since staleness and the
  seven-day review window both depend on the current date;
* **the filesystem** — shard files are shuffled on disk between runs, so nothing
  can depend on directory listing order or on which device wrote what;
* **timezone and locale** — the replay runs under ``TZ=Pacific/Auckland`` and a
  German locale, which is what would catch a stray ``strftime`` ``%B`` or a
  text-mode write turning ``\\n`` into ``\\r\\n``.
"""

from __future__ import annotations

import ast
import locale
import os
import shutil
import time
from datetime import date
from pathlib import Path

import pytest

from agent import projection
from agent import vault as vault_mod
from agent.config import SyncProfile
from agent.events import log
from agent.vault import Vault

from .conftest import claim, confirm, correct, ingested, merge, note, on_day

AS_OF = "2026-09-30T00:00:00Z"
DEVICES = ("elwood-laptop", "elwood-phone", "clinic-tablet")

DERIVED_DIRS = ("wiki", ".agent")

#: The caches of work in progress are skipped — see
#: ``agent.vault.NON_DETERMINISTIC_DERIVED``, which carries the reason each one
#: earns the exclusion. They are not merely awkward to reproduce: none of them
#: holds a fact, and ``test_http_index_is_disposable`` deletes all of them and
#: requires these same bytes to come back unchanged.


def _mixed_stream() -> list:
    """A stream with one of everything the projection has to handle."""
    laptop, phone, tablet = DEVICES
    events = [
        ingested(laptop, "a3f91c", ts=on_day(2), artifact_ts="2026-06-04T00:00:00Z"),
        ingested(phone, "77b210", ts=on_day(3), mime="application/pdf"),
        ingested(tablet, "cc9012", ts=on_day(4), mime="audio/webm", source="recorder"),
        note(phone, ts=on_day(4, hour=18), text="Headaches started around Easter."),
    ]

    # A confirmed medication with a countable supply.
    dose = claim(
        laptop,
        "med:perindopril",
        "dose",
        {"amount": 5, "unit": "mg", "frequency": "daily"},
        ts=on_day(2, hour=10),
        occurred={"value": "2026-06-04", "precision": "day", "uncertainty_days": 0},
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
    )
    events += [dose, confirm(laptop, dose.id, ts=on_day(2, hour=11))]

    # A correction, contradicted later by a re-extraction.
    misread = claim(
        phone,
        "med:atorvastatin",
        "dose",
        "2mg daily",
        ts=on_day(3, hour=10),
        artifact="77b210",
        occurred={"value": "2026-08-15"},
    )
    events += [
        misread,
        correct(phone, value="20mg daily", target=misread.id, ts=on_day(3, hour=12)),
    ]
    reread = claim(
        tablet,
        "med:atorvastatin",
        "dose",
        "40mg daily",
        ts=on_day(9, hour=10),
        artifact="77b210",
        occurred={"value": "2026-08-15"},
    )
    events += [reread, confirm(tablet, reread.id, ts=on_day(9, hour=11))]

    # Two equal sources that cannot be told apart: a conflict, rendered as one.
    first = claim(
        laptop, "med:metformin", "dose", "500mg twice daily", ts=on_day(5), artifact="a3f91c",
        occurred={"value": "2026-07-01"},
    )
    second = claim(
        phone, "med:metformin", "dose", "850mg twice daily", ts=on_day(5, hour=11),
        artifact="77b210", occurred={"value": "2026-07-01"},
    )
    events += [
        first,
        confirm(laptop, first.id, ts=on_day(6)),
        second,
        confirm(phone, second.id, ts=on_day(6, hour=11)),
    ]

    # A high-consequence claim nobody has confirmed, and a medium one old enough
    # to have applied itself.
    events.append(
        claim(laptop, "allergy:penicillin", "reaction", "anaphylaxis", ts=on_day(7),
              tier="patient-reported")
    )
    onset = claim(
        phone, "problem:hypertension", "onset", "gradual", ts=on_day(1),
        tier="patient-reported", artifact="cc9012",
        occurred={"value": "2026-04-05", "precision": "month", "uncertainty_days": 14},
    )
    events.append(onset)

    # A merge and a person.
    brand = claim(tablet, "med:panadol", "dose", "500mg as needed", ts=on_day(8),
                  tier="patient-reported", artifact="cc9012")
    generic = claim(tablet, "med:paracetamol", "dose", "500mg as needed", ts=on_day(8, hour=11),
                    tier="patient-reported", artifact="cc9012")
    events += [
        brand,
        confirm(tablet, brand.id, ts=on_day(8, hour=12)),
        generic,
        confirm(tablet, generic.id, ts=on_day(8, hour=13)),
        merge(laptop, "med:panadol", "med:paracetamol", ts=on_day(10)),
    ]
    prescriber = claim(laptop, "person:dr-nguyen", "role", "General practitioner",
                       ts=on_day(2, hour=12), tier="patient-reported")
    events.append(prescriber)

    return sorted(events, key=lambda event: event.sort_key)


def _write_shards(root: Path, events) -> None:
    for event in events:
        log.append(root / "events", event, SyncProfile.LOCAL)


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every derived byte in the vault, keyed by vault-relative path."""
    found: dict[str, bytes] = {}
    for name in DERIVED_DIRS:
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


@pytest.fixture
def populated(vault_root, identity):
    _write_shards(vault_root, _mixed_stream())
    return vault_root


def test_rebuild_is_byte_identical(populated, identity):
    vault = Vault.open(populated, identity=identity)
    projection.rebuild(vault, as_of=AS_OF)
    before = _snapshot(populated)
    assert before, "the rebuild produced no derived files at all"

    for name in DERIVED_DIRS:
        shutil.rmtree(populated / name, ignore_errors=True)
    vault_mod.scaffold(populated)

    projection.rebuild(Vault.open(populated, identity=identity), as_of=AS_OF)
    assert _snapshot(populated) == before


def test_rebuild_is_identical_after_shards_are_shuffled(populated, identity):
    """Nothing may depend on the order the shard files are discovered in."""
    vault = Vault.open(populated, identity=identity)
    projection.rebuild(vault, as_of=AS_OF)
    before = _snapshot(populated)

    for name in DERIVED_DIRS:
        shutil.rmtree(populated / name, ignore_errors=True)
    vault_mod.scaffold(populated)

    # Rewrite the shards under names that sort the other way, so the union read
    # encounters the devices in a different order.
    events_dir = populated / "events"
    shards = sorted(events_dir.glob("*.jsonl"))
    contents = {path.name: path.read_bytes() for path in shards}
    for path in shards:
        path.unlink()
    for name in sorted(contents, reverse=True):
        (events_dir / name).write_bytes(contents[name])

    projection.rebuild(Vault.open(populated, identity=identity), as_of=AS_OF)
    assert _snapshot(populated) == before


#: Names for one non-English locale, as each platform spells it. A locale that
#: cannot be set demonstrates nothing, and on Windows the POSIX spelling never
#: works — which is how this leg went untested there for eleven phases.
GERMAN = ("de_DE.UTF-8", "de_DE.utf8", "de_DE", "German_Germany.1252", "German")


def _german(locale_module) -> str | None:
    for name in GERMAN:
        try:
            locale_module.setlocale(locale_module.LC_ALL, name)
        except locale_module.Error:
            continue
        return name
    return None


def test_rebuild_is_identical_under_another_timezone_and_locale(populated, identity, monkeypatch):
    """Neither the timezone nor the locale may reach the generated bytes.

    A ``strftime('%B')`` would render "Juni" here, and a text-mode write would
    render CRLF on a platform that translates newlines. Both would be invisible
    on the machine that wrote them and would break the guarantee for everyone
    else.
    """
    vault = Vault.open(populated, identity=identity)
    projection.rebuild(vault, as_of=AS_OF)
    before = _snapshot(populated)

    for name in DERIVED_DIRS:
        shutil.rmtree(populated / name, ignore_errors=True)
    vault_mod.scaffold(populated)

    monkeypatch.setenv("TZ", "Pacific/Auckland")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    if hasattr(time, "tzset"):
        time.tzset()
    previous = locale.setlocale(locale.LC_ALL)
    try:
        if _german(locale) is None:
            # A locale is how this test *demonstrates* the leak, so without one
            # it can demonstrate nothing. CI installs one on every platform, so
            # this is reached only on a machine that has none — and there the
            # two tests below still hold the same guarantee without a locale at
            # all. The skip is reported by name on every CI run
            # (packaging/ci_invariants.py), because a guarantee that quietly
            # skipped reads exactly like one that held.
            pytest.skip(
                "no locale but the machine's own could be set, so a locale cannot be "
                "shown reaching the bytes here; the two tests below check the same "
                "guarantee without one"
            )
        projection.rebuild(Vault.open(populated, identity=identity), as_of=AS_OF)
    finally:
        locale.setlocale(locale.LC_ALL, previous)
        if hasattr(time, "tzset"):
            os.environ.pop("TZ", None)
            time.tzset()

    assert _snapshot(populated) == before


def test_generated_files_use_lf_endings_and_end_with_one_newline(populated, identity):
    projection.rebuild(Vault.open(populated, identity=identity), as_of=AS_OF)
    for rel, data in _snapshot(populated).items():
        assert b"\r" not in data, f"{rel} contains a carriage return"
        assert data.endswith(b"\n"), f"{rel} does not end with a newline"
        assert not data.endswith(b"\n\n"), f"{rel} ends with a blank line"


def test_the_caches_do_not_reach_the_comparison(populated, identity):
    """A cache appearing in the vault must not change what is compared.

    The exclusion is only sound if it is actually applied, and the failure mode
    if it is not — a rebuild that stops being byte-identical the moment someone
    captures a file — would look like a broken invariant rather than a broken
    test.
    """
    vault = Vault.open(populated, identity=identity)
    projection.rebuild(vault, as_of=AS_OF)
    before = _snapshot(populated)

    # The shapes a running server leaves behind.
    (populated / ".agent" / "jobs.jsonl").write_text(
        '{"id": "job-1", "artifact": "a3f91c", "state": "queued"}\n', encoding="utf-8"
    )
    (populated / ".agent" / "index.sqlite").write_bytes(b"SQLite format 3\x00")
    (populated / ".agent" / "logs" / "agent.log").write_text("noise\n", encoding="utf-8")

    projection.rebuild(Vault.open(populated, identity=identity), as_of=AS_OF)
    assert _snapshot(populated) == before


def test_rebuild_needs_nothing_but_raw_and_events(populated, identity):
    """The derived directories are genuinely derived: deleting them loses nothing."""
    vault = Vault.open(populated, identity=identity)
    first = projection.rebuild(vault, as_of=AS_OF)

    for name in DERIVED_DIRS:
        shutil.rmtree(populated / name, ignore_errors=True)
    vault_mod.scaffold(populated)

    second = projection.rebuild(Vault.open(populated, identity=identity), as_of=AS_OF)
    assert second.projection.stats() == first.projection.stats()
    assert sorted(second.projection.files) == sorted(first.projection.files)


# --- the same guarantee, on machines where no other locale can be set --------


def test_month_names_come_from_our_own_table_whatever_the_locale():
    """The rendered month is English on a German machine, with no rebuild needed.

    The test above shows a whole rebuild coming out identical under another
    locale, which is the real guarantee — but it can only run where a second
    locale exists. This one needs none: it sets what it can and asserts the
    words either way.
    """
    from agent.projection import dates

    previous = locale.setlocale(locale.LC_ALL)
    try:
        _german(locale)  # whether or not it takes
        assert dates.render_date(date(2026, 6, 4)) == "4 June 2026"
        assert dates.render_month(2026, 12) == "December 2026"
    finally:
        locale.setlocale(locale.LC_ALL, previous)


#: ``strftime`` directives whose output depends on the machine's locale. Month
#: and weekday names are the ones a date renderer reaches for first.
LOCALE_DEPENDENT = ("%B", "%b", "%A", "%a", "%p", "%c", "%x", "%X")


def _locale_dependent_formats(tree: ast.AST) -> list[tuple[int, str]]:
    """Every ``strftime`` in *tree* whose format names a translated field.

    Read from the syntax rather than the text, so that the comments explaining
    why ``%B`` is forbidden are not themselves reported as using it.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "strftime"):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                for directive in LOCALE_DEPENDENT:
                    if directive in argument.value:
                        found.append((node.lineno, argument.value))
    return found


def test_nothing_formats_a_date_through_a_locale():
    """A guarantee held by construction, checked by construction.

    ``MONTH_NAMES`` exists so a rendered date reads the same everywhere.
    Nothing stops a later caller reaching for ``%B`` again, except this.
    """
    offenders: list[str] = []
    package = Path(__file__).resolve().parent.parent / "agent"
    for path in sorted(package.rglob("*.py")):
        if "/static_files/" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line_number, fmt in _locale_dependent_formats(tree):
            offenders.append(
                f"{path.relative_to(package.parent)}:{line_number}: strftime({fmt!r}) "
                f"renders in whatever language the machine is set to"
            )
    assert not offenders, "\n  " + "\n  ".join(offenders)


def test_the_locale_walker_catches_one():
    assert _locale_dependent_formats(ast.parse("when.strftime('%d %B %Y')"))
    assert not _locale_dependent_formats(ast.parse("when.strftime('%Y-%m-%d')"))
