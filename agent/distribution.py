"""How this copy of the program was installed, and what that lets a message say.

Two installations exist. **The app** is a frozen build downloaded from a release
page: no venv, no pip, and no terminal its owner necessarily knows how to open.
**A pip install** is someone who typed ``pip install``, and so has a terminal
and knows it.

Every instruction a person reads has to be one they can follow. So:

- A **command** (``health-agent …``, ``pip install …``) is only ever shown on a
  pip install, through :func:`for_terminal`. The app gets the in-app path
  instead, written beside it. ``tests/test_no_terminal_in_the_app.py`` walks
  every string literal in the package and fails on a command outside that call.
- A library **the app bundles** that fails to import is a fault in the build,
  not something the owner can fix, and :func:`missing_library` says so rather
  than sending a non-technical person to run pip.
- Text that is **stored** — a job's reason, a wiki footnote, a note in an event
  — carries no command either way. It is written on one machine and read on
  another, and the vault syncs between a pip install and the app as readily as
  between two apps. What is true of the writer's installation is not true of
  the reader's.
"""

from __future__ import annotations

import sys

#: Where a person gets a fresh copy of the app. Named, not linked: a link in a
#: message is a thing to click, and this program makes no network calls.
RELEASES = "the Releases page of the project on GitHub"


def packaged() -> bool:
    """Whether this is the downloaded app rather than a pip install."""
    return bool(getattr(sys, "frozen", False))


def for_terminal(text: str) -> str:
    """*text* on a pip install, nothing in the app.

    For the part of a message that names a command. The rest of the message
    must already make sense on its own, with the in-app path in it.
    """
    return "" if packaged() else text


def missing_library(
    library: str, extra: str | None, consequence: str, stored: bool = False
) -> str:
    """A bundled library failed to import. What to say, by installation.

    *consequence* is what does not work as a result, as a sentence fragment
    starting lower-case, and is the same on both. *stored* is for text that is
    kept — a job's reason, a note in an event — which on a pip install states
    the fact and leaves the command to the terminal that is reading it.
    """
    if packaged():
        return (
            f"{consequence}: {library} should have come with this app and did not "
            f"load, which is a fault in how this copy was built rather than anything "
            f"you did. Nothing in your record is affected. Downloading the app again "
            f"from {RELEASES} should fix it; if it does not, please report it there."
        )
    if stored:
        return f"{consequence}: {library} is not installed on the computer that tried."
    target = f"'health-agent[{extra}]'" if extra else "health-agent"
    return for_terminal(
        f"{consequence}: {library} is not installed. Install it with `pip install {target}`."
    )
