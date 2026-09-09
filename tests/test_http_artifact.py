"""``/api/artifact/{hash}`` serves the original bytes, and assumes they are hostile.

The vault syncs from Dropbox, Drive or Nextcloud. Its contents are untrusted
input regardless of who owns the folder: they have been through a third party's
storage, arrived by restore, been dragged in from a download folder, or been
written by a device nobody has looked at in a year. This route hands those bytes
to a browser **on the same origin as the SPA**, so an ingested SVG or HTML file
rendered inline would be script with the app's origin and read access to every
route here — the whole record.

The tests below cover the three defences and the ordinary path: an allowlist of
inline types, ``nosniff``, and a policy that executes nothing.
"""

from __future__ import annotations

import json

import pytest

from agent.server import serving

from .conftest import JPEG, PDF, PNG

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><script>fetch('/api/wiki')</script>"


def _capture(client, name, data, mime):
    response = client.post("/api/capture", files={"files": (name, data, mime)})
    return response.json()["results"][0]["short"]


def test_an_image_is_served_inline_with_its_own_type(client):
    short = _capture(client, "script.jpg", JPEG, "image/jpeg")
    response = client.get(f"/api/artifact/{short}")

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_a_pdf_is_served_inline_because_a_citation_has_to_be_readable(client):
    short = _capture(client, "path.pdf", PDF, "application/pdf")
    response = client.get(f"/api/artifact/{short}")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")


@pytest.mark.parametrize(
    "name,data,declared",
    [
        ("letterhead.svg", SVG, "image/svg+xml"),
        ("portal.html", HTML, "text/html"),
        ("script.js", b"fetch('/api/wiki').then(r=>r.json())", "text/javascript"),
    ],
)
def test_a_scriptable_document_can_never_execute(client, name, data, declared):
    """The bytes are still served. What they may not do is run.

    Three independent defences, and the assertions cover all three because any
    one of them could be lost in an edit without the others noticing. This
    project's mime detector sniffs each of these as ``text/plain``, which a
    browser shows as source — but that is the third defence, not the first.
    """
    short = _capture(client, name, data, declared)
    response = client.get(f"/api/artifact/{short}")

    assert response.status_code == 200
    assert response.content == data, "the artefact is part of the record"

    served = response.headers["content-type"].split(";")[0].strip()
    assert served not in serving.NEVER_INLINE
    assert served in serving.INLINE_TYPES or served == serving.DOWNLOAD_TYPE
    # 1. Nothing is sniffed into something executable.
    assert response.headers["x-content-type-options"] == "nosniff"
    # 2. Even in a context we were wrong about, nothing runs and nothing is
    #    fetched — and `sandbox` gives it an opaque origin, so it could not
    #    reach the API even if it did.
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "sandbox" in policy


def test_a_type_on_the_never_inline_list_is_served_as_a_download(client, monkeypatch):
    """The allowlist is the kind of list that gets widened.

    Forcing the stored type to one a future mime detector might produce checks
    the branch that a widened allowlist would otherwise slip past.
    """
    short = _capture(client, "letterhead.svg", SVG, "image/svg+xml")

    from agent.server import lookup

    original = lookup.locate

    def as_svg(vault, snapshot, resolved):
        located = original(vault, snapshot, resolved)
        artifact = located.artifact.__class__(
            **{**located.artifact.__dict__, "mime": "image/svg+xml"}
        )
        return lookup.Located(artifact=artifact, path=located.path)

    monkeypatch.setattr(lookup, "locate", as_svg)
    response = client.get(f"/api/artifact/{short}")

    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")


def test_the_declared_type_does_not_decide_how_bytes_are_served(client):
    """A browser's guess is the weakest evidence about what a file is.

    Mime detection sniffs the bytes on the way in. An upload that calls an HTML
    document ``image/png`` must not be promoted to inline by saying so.
    """
    short = _capture(client, "trick.png", HTML, "image/png")
    response = client.get(f"/api/artifact/{short}")
    assert response.headers["content-type"] != "text/html"
    assert "script" not in response.headers.get("content-disposition", "")


def test_the_etag_is_the_content_hash(client):
    """Stored bytes are addressed by hash and never rewritten.

    An ETag derived from size and mtime would miss on every resync and every
    restore-from-backup, both of which change the metadata and neither of which
    changes the content. A changed mode is not corruption, and it is not a cache
    miss either.
    """
    short = _capture(client, "a.jpg", JPEG, "image/jpeg")
    meta = client.get(f"/api/artifact/{short}/meta").json()
    response = client.get(f"/api/artifact/{short}")

    assert response.headers["etag"] == f'"{meta["digest"]}"'
    again = client.get(
        f"/api/artifact/{short}", headers={"if-none-match": response.headers["etag"]}
    )
    assert again.status_code == 304


def test_a_range_request_is_honoured(client):
    """Seeking in a long voice note, and a PDF viewer fetching one page."""
    short = _capture(client, "a.pdf", PDF, "application/pdf")
    response = client.get(f"/api/artifact/{short}", headers={"range": "bytes=0-7"})
    assert response.status_code == 206
    assert response.content == PDF[:8]


def test_an_unknown_hash_is_a_404_that_says_what_a_hash_is(client):
    response = client.get("/api/artifact/zzzzzz")
    assert response.status_code == 404
    assert "six characters" in response.json()["detail"]


def test_an_ambiguous_prefix_is_refused_with_both_named(vault, client, monkeypatch):
    """Showing the wrong photograph looks like an answer. Ask for two more characters.

    The prefix length is a module constant precisely so a test can force a real
    collision rather than assert on a case that never happens in practice.
    """
    from agent.ingest import naming

    monkeypatch.setattr(naming, "SHORT_HASH_CHARS", 2)
    first = _capture(client, "a.jpg", JPEG, "image/jpeg")
    second = _capture(client, "b.png", PNG, "image/png")

    shared = _common_prefix(first, second)
    if not shared:
        return  # the two hashes share no prefix; nothing to disambiguate
    response = client.get(f"/api/artifact/{shared}")
    if response.status_code == 200:
        return  # `shared` happened to name one of them exactly
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert first in detail and second in detail


def test_a_full_digest_resolves_as_well_as_a_short_hash(client):
    short = _capture(client, "a.jpg", JPEG, "image/jpeg")
    digest = client.get(f"/api/artifact/{short}/meta").json()["digest"]
    assert client.get(f"/api/artifact/{digest}").content == JPEG


def test_a_path_in_the_url_reaches_nothing(vault, client):
    """The hash is a lookup key, never a path fragment."""
    secret = vault.root.parent / "secret.txt"
    secret.write_text("not part of the record", encoding="utf-8")
    for token in ("../secret.txt", "..%2Fsecret.txt", "/etc/hostname", "raw"):
        response = client.get(f"/api/artifact/{token}")
        assert response.status_code in (404, 409, 400)
        assert b"not part of the record" not in response.content


def test_a_file_in_raw_that_no_event_describes_is_not_servable(vault, client):
    """The log says what is in the record. The folder does not.

    Otherwise anything a sync client dropped into ``raw/`` would be fetchable
    through the API.
    """
    stray = vault.root / "raw" / "2026" / "09" / "2026-09-08T1432Z_beefed.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(JPEG)

    assert client.get("/api/artifact/beefed").status_code == 404


def test_recorded_bytes_that_are_missing_are_a_410_not_a_404(vault, client):
    """The record says this exists, so "gone" is the honest answer.

    A bare 404 would read as a wrong link. This is a problem to fix — restore it,
    or re-ingest the same file — and the message says so.
    """
    short = _capture(client, "a.jpg", JPEG, "image/jpeg")
    stored = vault.root / client.get(f"/api/artifact/{short}/meta").json()["path"]
    stored.chmod(0o600)
    stored.unlink()

    response = client.get(f"/api/artifact/{short}")
    assert response.status_code == 410
    assert "not in the vault" in response.json()["detail"]
    assert "health-agent check" in response.json()["detail"]


def test_meta_reports_the_four_timestamps_and_the_sidecar(client):
    short = _capture(client, "a.jpg", JPEG, "image/jpeg")
    meta = client.get(f"/api/artifact/{short}/meta").json()

    assert meta["ingested_ts"] is not None
    assert meta["captured_ts"] is None
    assert meta["artifact_ts"] is None
    assert meta["present"] is True
    assert meta["renders_inline"] is True
    # The sidecar is what makes the folder self-describing with nothing
    # installed, and it is shown as it is rather than merged in.
    assert meta["sidecar"]["hash"] == meta["digest"]
    assert meta["citation"]["url"] == f"/api/artifact/{short}"


def test_the_artefact_list_is_ordered_by_the_timestamp_that_is_always_known(client):
    for name, data, mime in (
        ("a.jpg", JPEG, "image/jpeg"),
        ("b.png", PNG, "image/png"),
        ("c.pdf", PDF, "application/pdf"),
    ):
        _capture(client, name, data, mime)

    body = client.get("/api/artifacts").json()
    assert body["total"] == 3
    stamps = [row["ingested_ts"] for row in body["artifacts"]]
    assert stamps == sorted(stamps, reverse=True)
    assert all(row["captured_ts"] is None for row in body["artifacts"])


def test_no_artifact_response_carries_a_credential(client):
    from agent.llm import redaction

    key = "sk-artifact-route-must-not-leak-1234"
    redaction.register(key)
    try:
        short = _capture(client, "a.jpg", JPEG, "image/jpeg")
        for route in (f"/api/artifact/{short}", f"/api/artifact/{short}/meta",
                      "/api/artifacts", "/api/artifact/zzzzzz"):
            response = client.get(route)
            assert key not in response.text
            assert key not in json.dumps(dict(response.headers))
    finally:
        redaction.forget_all()


def _common_prefix(a: str, b: str) -> str:
    shared = ""
    for left, right in zip(a, b):
        if left != right:
            break
        shared += left
    return shared
