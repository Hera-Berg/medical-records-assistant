"""The one line in the tray menu that says what the reader is doing.

The same words as the sidebar's (``ENDPOINT_WORDS`` in
``frontend/src/components/Sidebar.tsx``), so the tray and the page never
describe one state two ways. Shorter than a sentence because a menu item is
one line, and never a sentence that needs the page to make sense of it.
"""

from __future__ import annotations

from ..server import endpoint_state as es

WORDS = {
    es.WORKING: "Ready to read new files",
    es.UNREACHABLE: "Asleep — files wait safely",
    es.UNAUTHORISED: "Password rejected",
    es.MISCONFIGURED: "Set up incorrectly",
    es.BLIND: "Cannot read pictures",
    es.NOT_CONFIGURED: "Not set up yet",
    es.UNKNOWN: "Not checked yet",
    es.NOT_DOWNLOADED: "Needs a one-time download",
    es.SLEEPING: "Sleeping — wakes when you add something",
    es.STARTING: "Starting up",
    es.STOPPED: "Stopped — see Settings",
}


def reader_line(state: es.EndpointState) -> str:
    if state.state == es.WORKING and state.where == es.THIS_COMPUTER:
        words = "Reading on this computer"
    else:
        words = WORDS.get(state.state, WORDS[es.UNKNOWN])
    return f"Reader: {words}"
