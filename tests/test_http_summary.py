"""Preparing and printing the sheet over HTTP.

``GET /print/{id}`` is the route that matters most here and it is the one with
the fewest moving parts: a server-rendered document with no application chrome,
no login and no script. Many clinicians will not take a phone from a patient, so
the thing that actually gets read is paper — and the page that becomes that
paper cannot depend on a bundle loading or a fetch succeeding.

The export written beside it is checked for the property that makes it an
archival copy rather than a convenience: **it opens from the folder with nothing
running.** That means no script, no stylesheet link, no font, no absolute URL,
and every source pointing at a relative path that is really there.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import pytest

from agent.summary import store
from agent.summary.model import DEMO_WARNING

from .conftest import claim, confirm, ingested, on_day, reject


def _seed(vault, *, value="5mg daily"):
    """One confirmed dose, with the document behind it on disk."""
    device = vault.identity.id
    reading = claim(
        device,
        "med:perindopril",
        "dose",
        value,
        ts=on_day(2),
        artifact="a3f91c",
        artifact_ts=on_day(2),
    )
    events = [
        ingested(device, "a3f91c", ts=on_day(1)),
        reading,
        confirm(device, reading.id, ts=on_day(3)),
    ]
    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    # The citation resolves to a path in the folder, so the export's relative
    # links have something real to point at.
    original = vault.root / "raw" / "2026" / "09" / "2026-09-02T0914Z_a3f91c.jpg"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"\xff\xd8\xff\xe0not-a-real-photograph")
    return reading


def _prepare(client, **body):
    body.setdefault("question", "Should I stay on this dose?")
    body.setdefault("label", "cardiology")
    response = client.post("/api/summary", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- preparing ---------------------------------------------------------------


def test_an_empty_record_has_no_summaries(client):
    assert client.get("/api/summary").json()["summaries"] == []


def test_preview_writes_nothing_and_records_nothing(client, vault):
    _seed(vault)
    before = len(list(vault.read().events))

    body = client.post(
        "/api/summary/preview", json={"question": "why?", "label": "gp"}
    ).json()

    assert body["id"] == "preview"
    assert "5mg daily" in body["markdown"]
    assert len(list(vault.read().events)) == before
    assert list((vault.root / "exports").iterdir()) == []


def test_preparing_appends_one_event_and_writes_two_files(client, vault):
    _seed(vault)
    body = _prepare(client)

    events = [e for e in vault.read().events if e.type == store.EVENT_TYPE]
    assert len(events) == 1
    assert events[0].id == body["id"]
    assert events[0].actor == "user"

    markdown = vault.root / body["exports"]["markdown"]
    html = vault.root / body["exports"]["html"]
    assert markdown.is_file() and html.is_file()
    assert markdown.name == "2026-09-11_cardiology.md" or markdown.name.endswith(
        "_cardiology.md"
    )
    # The person who prepared it is told where in their folder it landed: the
    # folder is the record, and two files just appeared in it.
    assert body["exports"]["markdown"] in body["message"]


def test_the_question_is_recorded_as_the_patient_typed_it(client, vault):
    _seed(vault)
    asked = "Do I still need both of these? I have been getting headaches."
    body = _prepare(client, question=asked)
    event = next(e for e in vault.read().events if e.type == store.EVENT_TYPE)
    assert event.payload["question"] == asked
    assert asked in (vault.root / body["exports"]["markdown"]).read_text("utf-8")


def test_preparing_twice_on_one_day_never_overwrites(client, vault):
    _seed(vault)
    first = _prepare(client)
    second = _prepare(client)
    assert first["exports"]["markdown"] != second["exports"]["markdown"]
    assert (vault.root / first["exports"]["markdown"]).is_file()
    assert (vault.root / second["exports"]["markdown"]).is_file()


def test_the_second_sheet_measures_from_the_first(client, vault):
    _seed(vault)
    first = _prepare(client)
    second = _prepare(client)
    assert second["since_ts"] is not None
    assert "previous summary" in second["sections"][0]["subnote"]
    assert first["id"] != second["id"]


# --- the printed page --------------------------------------------------------


def test_the_print_page_is_a_document_not_an_app(client, vault):
    _seed(vault)
    body = _prepare(client)

    response = client.get(body["print_url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text

    # No script anywhere, and nothing to fetch. This page has to look the same
    # opened from a folder with no server as it does served from one.
    assert "<script" not in page
    assert "<link" not in page
    assert "http://" not in page and "https://" not in page
    assert "5mg daily" in page
    assert "Prepared" in page

    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src" not in policy or "'none'" in policy


def test_the_print_page_has_no_application_chrome(client, vault):
    _seed(vault)
    body = _prepare(client)
    page = client.get(body["print_url"]).text
    for chrome in ("Waiting for you", "Add something", "Settings", "Rebuild pages"):
        assert chrome not in page


def test_the_print_page_links_each_source_to_its_original(client, vault):
    _seed(vault)
    body = _prepare(client)
    page = client.get(body["print_url"]).text
    assert "/api/artifact/a3f91c" in page
    assert client.get("/api/artifact/a3f91c").status_code == 200


def test_an_unknown_summary_answers_in_html(client):
    response = client.get("/print/01J8F2K3M4N5P6Q7R8S9T0V1W2")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "not in your record" in response.text


# --- the archival copy -------------------------------------------------------


def test_the_exported_page_is_self_contained(client, vault):
    """It has to render as itself with nothing running.

    An export that needs a server to look like a document is not an archival
    copy of one. The links are relative into the folder, which is also what
    makes the evidence still followable when the app is gone.
    """
    _seed(vault)
    body = _prepare(client)
    page = (vault.root / body["exports"]["html"]).read_text("utf-8")

    assert "<script" not in page
    assert "<link" not in page
    assert "@import" not in page
    assert "http://" not in page and "https://" not in page
    assert "/api/" not in page

    links = _hrefs(page)
    assert links
    exports = vault.root / "exports"
    for href in links:
        assert href.startswith("../"), href
        assert (exports / unquote(href)).resolve().is_file(), href


def test_the_export_carries_its_own_dateline(client, vault):
    _seed(vault)
    body = _prepare(client)
    for key in ("markdown", "html"):
        text = (vault.root / body["exports"][key]).read_text("utf-8")
        assert "from a patient-held record" in text
        assert "does not interpret" in text


def test_a_demo_vault_marks_every_export_as_invented(client, vault):
    """The one export that could cause harm says so wherever it is read."""
    _seed(vault)
    (vault.root / "DEMO-DATA.md").write_text("invented\n", encoding="utf-8")
    body = _prepare(client)

    for key in ("markdown", "html"):
        assert DEMO_WARNING in (vault.root / body["exports"][key]).read_text("utf-8")
    assert DEMO_WARNING in client.get(body["print_url"]).text


# --- withdrawal --------------------------------------------------------------


def test_a_rejection_withdraws_the_printed_page(client, vault):
    """Rejected content never renders — not on a page, not in an export.

    The stored file stays where it is, because the user may want to know what
    they handed over; what stops is this application re-serving it.
    """
    reading = _seed(vault, value="50mg daily")
    body = _prepare(client)
    assert "50mg daily" in client.get(body["print_url"]).text

    vault.append(reject(vault.identity.id, reading.id, ts="2026-09-12T09:00:00Z"))

    page = client.get(body["print_url"])
    assert page.status_code == 200
    assert "withdrawn" in page.text
    assert "50mg daily" not in page.text

    detail = client.get(f"/api/summary/{body['id']}").json()
    assert detail["summary"] is None
    assert detail["withdrawn"] == 1
    assert "50mg" not in detail["message"]

    listed = client.get("/api/summary").json()["summaries"][0]
    assert listed["withdrawn"] == 1


# --- reading one back --------------------------------------------------------


def test_a_prepared_sheet_reads_back_with_its_markdown(client, vault):
    _seed(vault)
    body = _prepare(client)
    detail = client.get(f"/api/summary/{body['id']}").json()
    assert detail["withdrawn"] == 0
    assert detail["question"] == "Should I stay on this dose?"
    assert detail["markdown"] == (
        vault.root / body["exports"]["markdown"]
    ).read_text("utf-8")


def test_an_unknown_summary_is_a_404_with_a_sentence(client):
    response = client.get("/api/summary/01J8F2K3M4N5P6Q7R8S9T0V1W2")
    assert response.status_code == 404
    assert "no summary in this record" in response.json()["detail"]


def _hrefs(page: str) -> list[str]:
    import re

    return re.findall(r'href="([^"]+)"', page)
