"""Turn a pytest JUnit report into GitHub annotations, which are public.

A failing run's log needs a token to read. Annotations do not: they come back
from ``/repos/{owner}/{repo}/check-runs/{id}/annotations`` for anyone who can
see the repository. So the failures are written as workflow ``::error``
commands, and whoever is triaging — including a tool with no credentials — can
read what failed without being handed the log.

Only the message's first lines are carried. The whole traceback stays in the
log, where it belongs; this is the index to it.

    python packaging/ci_annotations.py ci-report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: GitHub shows the first ten annotations of a kind per step. Past that, one
#: line saying how many more there are beats nine that push it off the list.
LIMIT = 10
EXCERPT = 600


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def title(text: str) -> str:
    """A workflow command's properties are comma-separated and end at ``::``.

    A test id carries both — ``tests.test_x::test_y`` — so they are replaced
    rather than escaped, which the property syntax has no way to express.
    """
    return escape(text.replace("::", " \u2192 ").replace(",", ";"))


def main(path: Path) -> int:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        print(f"::error title=CI::the test report at {path} could not be read: {escape(str(exc))}")
        return 0

    problems = []
    for case in tree.iter("testcase"):
        for kind in ("failure", "error"):
            found = case.find(kind)
            if found is None:
                continue
            where = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
            message = (found.get("message") or "") + "\n" + (found.text or "")
            problems.append((kind, where, message.strip()[:EXCERPT]))

    for kind, where, message in problems[:LIMIT]:
        print(f"::error title={title(where)}::{kind}: {escape(message)}")
    if len(problems) > LIMIT:
        print(f"::error title=More failures::{len(problems) - LIMIT} further failures are in the log")
    print(f"{len(problems)} failing tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "ci-report.xml")))
