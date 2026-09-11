"""The consultation summary: preparing one, reading one back, printing one.

Five routes, and the important one is the last. ``GET /print/{id}`` is a plain
server-rendered page with no application chrome, no login and no script — "many
clinicians will not take a phone from a patient; a handed sheet of paper gets
read", and the page that becomes that sheet cannot depend on a bundle loading,
a React tree mounting, or a fetch succeeding.

**Preparing writes; nothing else does.** ``/api/summary/preview`` composes the
same sheet and appends nothing, so a patient can see exactly what they are about
to commit before an event and two files exist. Everything else here is a read.

**A withdrawn sheet is answered, not hidden.** If a claim a stored summary
rested on has since been rejected, the sheet is not re-rendered — rejected
content never renders, "not in entity pages, not in exports" — and the answer
explains that in a sentence which reproduces none of it. That is a legitimate
state rather than an error, so it comes back with a normal status and something
readable in it, the way every other routine state in this application does.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse

from ...summary import html as html_mod
from ...summary import markdown as md_mod
from ...summary import store as store_mod
from ...summary.store import SummaryError
from ..deps import get_state
from ..serving import API_HEADERS
from ..state import RecordState

router = APIRouter()

#: The print page runs no script and fetches nothing. It is one document with
#: its styles inline, and this says so in the browser rather than trusting it.
#: ``style-src 'unsafe-inline'`` is the whole of what it needs: a stylesheet
#: link would be a second request, and this page has to render identically when
#: it is opened as a file with no server at all.
PRINT_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)

PRINT_HEADERS = {
    "content-security-policy": PRINT_CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


def _payload(summary, markdown: bytes | None = None) -> dict[str, Any]:
    data = summary.to_dict()
    data["print_url"] = f"/print/{summary.id}"
    if markdown is not None:
        # The exact bytes the folder holds. Sent so the screen can show the
        # archival copy rather than a second rendering of the same facts that
        # might not agree with it.
        data["markdown"] = markdown.decode("utf-8")
    return data


@router.get("/api/summary")
def listing(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """Every sheet ever prepared, newest first.

    Carries each one's question — the patient's own words are how they will
    recognise the sheet they want — and how many of its entries have since been
    rejected, never what those said.
    """
    snapshot = state.snapshot()
    return {
        "summaries": store_mod.listing(snapshot.events),
        "as_of": snapshot.built_ts,
    }


@router.post("/api/summary/preview")
def preview(
    body: dict[str, Any] = Body(default_factory=dict),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """The sheet that would be prepared. Appends nothing, writes nothing."""
    snapshot = state.snapshot()
    try:
        summary = store_mod.compose(
            snapshot.events,
            as_of=snapshot.as_of,
            question=str(body.get("question") or ""),
            label=str(body.get("label") or ""),
            since=body.get("since") or None,
            demo=state.vault.is_demo,
        )
    except SummaryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _payload(summary, md_mod.render(summary))


@router.post("/api/summary")
def prepare(
    body: dict[str, Any] = Body(default_factory=dict),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Prepare a sheet: one event, then two files in ``exports/``.

    Taken under the record's lock, like every other write. The event is appended
    before the files are written — the log is the source of truth and an export
    with nothing in the log behind it is a document the record cannot explain.
    """
    with state.lock:
        snapshot = state.snapshot()
        try:
            prepared = store_mod.prepare(
                state.vault,
                as_of=snapshot.as_of,
                question=str(body.get("question") or ""),
                label=str(body.get("label") or ""),
                since=body.get("since") or None,
                events=snapshot.events,
                append=state.append,
            )
        except SummaryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    summary = prepared.summary
    data = _payload(summary, prepared.files[prepared.markdown_path])
    data["event"] = prepared.event.id
    data["exports"] = {
        "markdown": prepared.markdown_path,
        "html": prepared.html_path,
    }
    # Said plainly, because the folder is the record: the person who prepared
    # this should know two files just appeared in it and where they are.
    data["message"] = (
        f"Prepared. Your sheet is saved in your folder as "
        f"{prepared.markdown_path} and {prepared.html_path}, and the printable "
        f"page opens with no app running."
    )
    return data


@router.get("/api/summary/{summary_id}")
def one(summary_id: str, state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """One prepared sheet, re-rendered from the log as it stood at the time."""
    snapshot = state.snapshot()
    try:
        restored = store_mod.restore(snapshot.events, summary_id)
    except SummaryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    if restored.summary is None:
        return {
            "id": summary_id,
            "summary": None,
            "withdrawn": len(restored.withdrawn),
            "message": restored.explanation,
            "exports": restored.exports,
            "as_of": snapshot.built_ts,
        }
    data = _payload(restored.summary, md_mod.render(restored.summary))
    data["withdrawn"] = 0
    data["exports"] = restored.exports
    data["as_of"] = snapshot.built_ts
    return data


@router.get("/print/{summary_id}", include_in_schema=False)
def print_sheet(summary_id: str, state: RecordState = Depends(get_state)):
    """The A4 sheet, as a document. No chrome, no script, no login.

    The same renderer writes the file in ``exports/``; only the links differ,
    because here the originals are one route away and there they are a relative
    path into ``raw/``.
    """
    snapshot = state.snapshot()
    try:
        restored = store_mod.restore(snapshot.events, summary_id)
    except SummaryError as exc:
        return HTMLResponse(
            _plain_page("This summary is not in your record", str(exc)),
            status_code=404,
            headers=PRINT_HEADERS,
        )

    if restored.summary is None:
        # A normal status, deliberately. The sheet existed, the user prepared
        # it, and this page is the answer to what happened to it — not an error
        # to be interpreted. None of the withdrawn content is reproduced.
        return HTMLResponse(
            _plain_page("This summary has been withdrawn", restored.explanation),
            headers=PRINT_HEADERS,
        )

    return HTMLResponse(
        html_mod.render(restored.summary, html_mod.SERVER_LINKS),
        headers=PRINT_HEADERS,
    )


def _plain_page(title: str, message: str) -> str:
    """A page for the two cases where there is no sheet to print.

    Styled like the sheet and carrying nothing else: whoever followed this link
    was expecting a document, and a JSON error body in a browser tab is not an
    answer to anything.
    """
    from html import escape

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{html_mod.STYLE}</style></head>"
        f'<body><main class="sheet"><h1>{escape(title)}</h1>'
        f'<p class="dateline">{escape(message)}</p></main></body></html>\n'
    )


__all__ = ["router", "PRINT_HEADERS", "PRINT_CONTENT_SECURITY_POLICY"]
