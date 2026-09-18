"""Put the reader's own log into the annotations when a reader test fails.

The supervisor writes why a launch failed to ``reader.log`` beside the reader's
files. In CI that file is inside pytest's temporary directory and reachable
only by someone who can download the job's log or its artifacts — which needs a
credential. So when a run fails, the last lines of each log are written as
annotations, which do not.

Nothing here is guesswork about which check failed: it is the log saying.

    python packaging/ci_reader_logs.py pytest-tmp
"""

from __future__ import annotations

import sys
from pathlib import Path

from ci_annotations import _utf8_stdout, escape, title  # noqa: F401 - same directory

#: Logs are the reader's and the app's. Both are written outside any vault and
#: neither is ever run verbose, so neither carries a prompt or a key.
NAMES = ("reader.log", "reader.previous.log", "app.log")
TAIL = 4000
LIMIT = 8


def main(root: Path) -> int:
    _utf8_stdout()
    found = sorted(path for name in NAMES for path in root.rglob(name) if path.is_file())
    if not found:
        print(f"::error title=No reader log::nothing under {root} wrote one, so a failure to start said nothing")
        return 0
    for path in found[:LIMIT]:
        text = path.read_text(encoding="utf-8", errors="replace")[-TAIL:]
        # Which test's log this is: the directory pytest made for it. Without
        # that, a log cannot be matched to the failure it belongs to.
        try:
            where = path.relative_to(root).parts[0]
        except ValueError:  # pragma: no cover - rglob always returns descendants
            where = path.parent.name
        # An empty log is evidence: the child printed nothing at all, which is
        # different from printing something unrecognised.
        body = escape(text) if text.strip() else "(empty — the child printed nothing)"
        print(f"::error title={title(where + ' / ' + path.name)}::{body}")
    print(f"{len(found)} log(s) found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "pytest-tmp")))
