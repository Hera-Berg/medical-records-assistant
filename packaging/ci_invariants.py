"""Say, positively, what the invariant tests did on this machine.

"Not in the failure list" and "ran and passed" are different things: a test
that was never collected, or that skipped itself because the machine lacked
something, appears in no list of failures. The invariants in ``CLAUDE.md`` are
the ones every other guarantee rests on, so each is reported by name with what
actually happened to it — passed, failed, skipped, or never collected — whether
the run succeeded or not.

A skip is reported as loudly as a failure. The locale leg of the determinism
test skips itself where ``de_DE.UTF-8`` is missing, which is every Windows
machine, and that is exactly the case this file exists to make visible.

    python packaging/ci_invariants.py ci-report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ci_annotations import _utf8_stdout, escape

#: The guarantees, by test. `CLAUDE.md`'s "Testing" section names these; the
#: determinism module is listed test by test because its legs cover different
#: environmental leaks — the clock, the newlines, the locale.
INVARIANTS = (
    "test_rebuild_deterministic.py::test_rebuild_is_byte_identical",
    "test_rebuild_deterministic.py::test_rebuild_is_identical_after_shards_are_shuffled",
    "test_rebuild_deterministic.py::test_rebuild_is_identical_under_another_timezone_and_locale",
    "test_rebuild_deterministic.py::test_generated_files_use_lf_endings_and_end_with_one_newline",
    "test_rebuild_deterministic.py::test_rebuild_needs_nothing_but_raw_and_events",
    # These two need no second locale, so they hold the same guarantee on a
    # machine where the one above cannot demonstrate it.
    "test_rebuild_deterministic.py::test_month_names_come_from_our_own_table_whatever_the_locale",
    "test_rebuild_deterministic.py::test_nothing_formats_a_date_through_a_locale",
    "test_writes_are_binary.py::test_nothing_in_the_package_writes_through_text_mode",
    "test_correction_survives_reextraction.py::test_correction_survives_reextraction",
    "test_consequence_gate.py::test_high_consequence_never_autoapplies",
    "test_medication_lifecycle.py::test_no_silent_drop",
    "test_reconciliation.py::test_conflicting_sources_render_both",
)

PASSED, FAILED, SKIPPED, MISSING = "passed", "failed", "skipped", "never collected"


def outcomes(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    tree = ET.parse(path)
    for case in tree.iter("testcase"):
        module = (case.get("classname") or "").split(".")[-1]
        name = (case.get("name") or "").split("[")[0]
        key = f"{module}.py::{name}"
        if key not in INVARIANTS:
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            found[key] = FAILED
        elif case.find("skipped") is not None:
            # A skip wins over a pass among parametrised cases: the point is
            # whether the guarantee was exercised everywhere it claims to be.
            found[key] = SKIPPED
        else:
            found.setdefault(key, PASSED)
    return found


def main(path: Path) -> int:
    _utf8_stdout()
    try:
        found = outcomes(path)
    except (OSError, ET.ParseError) as exc:
        print(f"::error title=Invariants::the test report could not be read: {escape(str(exc))}")
        return 1

    lines = [f"{name}: {found.get(name, MISSING)}" for name in INVARIANTS]
    unproven = [name for name in INVARIANTS if found.get(name, MISSING) != PASSED]
    body = escape("\n".join(lines))
    if unproven:
        print(f"::error title=Invariants not proven on this machine::{body}")
    else:
        print(f"::notice title=Invariants all passed on this machine::{body}")
    for line in lines:
        print(line)
    return 1 if unproven else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "ci-report.xml")))
