"""Every sentence the reader on this computer can put on a screen, by code.

The same rule as :mod:`agent.server.endpoint_state`: nothing shown in a browser
is read out of an exception, a log line or anything the process printed. A code
is chosen where the thing happened and the words are chosen here. Local facts
that a person needs — the log file's path, a byte count — travel in their own
fields beside the sentence, never inside it.
"""

from __future__ import annotations

from . import platforms

#: No build is pinned for this kind of machine.
UNSUPPORTED = "unsupported"
#: The files are not all here and verified yet.
NOT_DOWNLOADED = "not-downloaded"
#: Files are here; the process is not running, because nothing has needed it or
#: because it was idle long enough to be put to sleep.
SLEEPING = "sleeping"
#: Launching, loading, or being restarted after a crash.
STARTING = "starting"
#: Running, identified, and holding a key the client may use.
READY = "ready"
#: Stopped and staying stopped until a person acts.
STOPPED = "stopped"
#: Another process on this machine owns the reader.
ELSEWHERE = "in-use-elsewhere"

TERMINAL = frozenset({STOPPED, UNSUPPORTED, ELSEWHERE})

MESSAGES: dict[str, str] = {
    "unsupported-platform": (
        "There is no build of the reader for this kind of computer. Documents can "
        "still be read on another computer you own — choose that in Settings."
    ),
    "not-downloaded": (
        "The files that read documents on this computer are not here yet. "
        "Everything you add is kept, and waits to be read once they are."
    ),
    "downloading": (
        "The files that read documents are downloading. Everything you add waits, "
        "and is read once they have arrived."
    ),
    "sleeping": (
        "Sleeping to free memory — wakes when you add something. The first "
        "document after it wakes takes a few seconds longer."
    ),
    "starting": "Starting up. The first document takes a little longer than the rest.",
    "restarting": (
        "The reader stopped unexpectedly and is being started again. Nothing you "
        "added is lost."
    ),
    "ready": "Reading on this computer.",
    "crashed-repeatedly": (
        "The reader stopped three times in ten minutes, so it has not been started "
        "again. Nothing you added is lost. Its log says what happened; try again "
        "from Settings."
    ),
    "out-of-memory": (
        "The reader was stopped by the system, most likely because this computer "
        "ran out of memory. Close other programs and try again from Settings, or "
        "read documents on another computer."
    ),
    "failed-to-start": (
        "The reader could not start. Nothing you added is lost. Its log says why; "
        "try again from Settings."
    ),
    "not-its-port": (
        "Something other than the reader answered on its port, so the reader's key "
        "was not sent and nothing was read. Try again from Settings."
    ),
    "wrong-build": (
        "The program in the reader's folder is not the build this app pins, so it "
        "was stopped before it read anything. Download the files again."
    ),
    "no-vision": (
        "The reader started without being able to see images, so it was stopped "
        "before it read anything — every document would have been silently "
        "ignored. Download the files again."
    ),
    "in-use-elsewhere": (
        "Another copy of this app on this computer is already running the reader. "
        "Use that one, or close it and try again."
    ),
}

#: What each download failure means, for the download screen.
DOWNLOAD_MESSAGES: dict[str, str] = {
    "no-space": (
        "There is not enough free space for the files, so nothing was downloaded. "
        "Free some space and try again."
    ),
    "hash-mismatch": (
        "A file arrived with different contents from the ones this app pins, so it "
        "was deleted and not used. Try again; if it happens twice, something "
        "between you and the download site is changing files."
    ),
    "host-not-allowed": (
        "The download was redirected to a site this app does not fetch from, so "
        "nothing was requested from it."
    ),
    "network": (
        "The connection dropped. What had arrived is kept — continuing picks up "
        "where it stopped."
    ),
    "http-status": "The download site refused the request. Try again later.",
    "cancelled": "Stopped. What had arrived is kept — continuing picks up where it stopped.",
    "inside-vault": (
        "The folder for the reader's files is inside your record's folder, which "
        "syncs. The files were not downloaded there."
    ),
    "archive-layout": (
        "The downloaded program did not unpack the way this app expects, so it was "
        "not used."
    ),
    "unexpected": "The download stopped for a reason this app did not expect. Try again.",
}


def speed_estimate(platform: str | None) -> str:
    """What to expect before this machine has read anything to measure.

    The processor range is wide on purpose. Measured in development on a
    low-power laptop chip (Core Ultra 7 155U), one photographed page was about
    1,650 prompt tokens at 16 a second with every thread in use — well over a
    minute and a half before the answer began. Faster processors have not been
    measured. Once this machine has read something, the screen says what it
    actually took instead. The Apple Silicon figure is from MODELS.md and has not
    been measured here.
    """
    if platform == platforms.MACOS_ARM64:
        return "about 10 to 30 seconds a document"
    return "about 1 to 4 minutes a document"


def message(reason: str | None) -> str:
    return MESSAGES.get(reason or "", MESSAGES["failed-to-start"])
