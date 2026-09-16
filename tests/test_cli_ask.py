"""Phase 10 from the terminal: ``health-agent ask``.

The record has to be usable with no server running — that is what "the folder
outlives the app" means in practice — and a question is no exception. This
command answers from the folder, prints footnotes in the same shape the wiki
uses so a citation can be followed by hand into ``raw/``, and writes nothing at
all.

``--offline`` is its own state and not a stand-in for a missing endpoint. "You
asked me not to contact it" and "no computer is set up" are different sentences,
because the second sends somebody to fix a setting that is already correct.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from agent import cli

from .conftest import claim, confirm, ingested, on_day, reject


@pytest.fixture
def seeded(vault):
    device = vault.identity.id
    events = [ingested(device, "a3f91c", ts=on_day(2))]
    dose = claim(device, "med:perindopril", "dose", "5mg daily", ts=on_day(3),
                 artifact="a3f91c", occurred={"value": "2026-09-02", "precision": "day"})
    events += [dose, confirm(device, dose.id, ts=on_day(3, hour=12))]

    misheard = claim(device, "problem:alcohol-dependence", "name", "alcohol dependence",
                     ts=on_day(4), artifact="a3f91c", tier="patient-reported",
                     subject_name="alcohol dependence")
    events += [misheard, reject(device, misheard.id, ts=on_day(5))]

    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    return vault


def run(root, *args):
    out = io.StringIO()
    code = cli.main(["ask", "--vault", str(root), *args], out=out)
    return code, out.getvalue()


def fingerprint(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_asking_from_the_terminal_writes_nothing(seeded):
    before = fingerprint(seeded.root)

    run(seeded.root, "--offline", "what dose of perindopril am I on")
    run(seeded.root, "--offline", "what is the capital of France")

    assert fingerprint(seeded.root) == before


def test_what_was_found_is_printed_with_footnotes_that_point_into_raw(seeded):
    code, text = run(seeded.root, "--offline", "what dose of perindopril am I on")

    assert code == 0
    assert "5mg daily" in text
    assert "[^a3f91c]" in text
    # The footnote resolves to a relative path in the folder, exactly as a wiki
    # citation does — followable by hand, with no server and no app.
    assert "raw/2026/09/" in text


def test_offline_is_not_reported_as_a_missing_endpoint(seeded):
    _code, text = run(seeded.root, "--offline", "what dose of perindopril am I on")

    assert "because you asked not to" in text
    assert "No computer is set up" not in text


def test_a_refused_question_points_at_the_summary_and_retrieves_nothing(seeded):
    code, text = run(seeded.root, "--offline", "should I be worried about this dose")

    assert code == 0
    assert "not a question your record can answer" in text
    assert "Take a sheet to your appointment" in text
    assert "5mg" not in text


def test_an_empty_retrieval_says_only_that(seeded):
    _code, text = run(seeded.root, "--offline", "what is the capital of France")

    assert text.strip() == "Nothing in your record covers that."


def test_rejected_content_is_in_no_line_of_the_output(seeded):
    for question in ("what problems do I have", "list everything in my record"):
        _code, text = run(seeded.root, "--offline", question)
        assert "alcohol" not in text.lower(), question


def test_json_output_is_machine_readable_and_carries_the_citations(seeded):
    _code, text = run(seeded.root, "--offline", "--json", "what dose of perindopril am I on")

    body = json.loads(text)
    assert body["state"] == "offline"
    assert body["box"] == "asked-not-to"
    assert body["found"]
    assert all(entry["cite"] for entry in body["found"])


def test_json_output_has_nothing_prose_shaped_above_it(seeded):
    """A "found 3" line above the object makes the whole stream unparseable."""
    _code, text = run(seeded.root, "--offline", "--json", "what am I taking")

    assert text.lstrip().startswith("{")
