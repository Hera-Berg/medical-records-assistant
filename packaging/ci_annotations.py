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

import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: GitHub shows ten annotations of a kind per step. The first is spent listing
#: *every* failing test, so that a cap can never hide one — a failure nobody can
#: see is the state this whole file exists to end. The rest carry detail.
LIMIT = 9
EXCERPT = 600
#: How much of what the failing test logged is carried with it.
CAPTURED = 900


def _utf8_stdout() -> None:
    """Write UTF-8 whatever the console claims.

    Windows gives Python the console's legacy encoding, and both the messages
    this project writes (— and ••••) and the ids below (→) are outside it. The
    first annotation printed then raised UnicodeEncodeError and the step that
    reports failures failed instead, which is how a whole platform's failures
    went unreported once already.
    """
    stream = getattr(sys.stdout, "reconfigure", None)
    if stream is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    elif isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no cover - older streams
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def title(text: str) -> str:
    """A workflow command's properties are comma-separated and end at ``::``.

    A test id carries both — ``tests.test_x::test_y`` — so they are replaced
    rather than escaped, which the property syntax has no way to express.
    """
    return escape(text.replace("::", " \u2192 ").replace(",", ";"))


def main(path: Path) -> int:
    _utf8_stdout()
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
            # What the code under test logged while failing, which is where a
            # process that would not start says what stopped it. Kept short and
            # last: the assertion is what a reader wants first.
            captured = "\n".join(
                (section.text or "").strip()
                for name in ("system-out", "system-err")
                for section in case.findall(name)
            ).strip()
            if captured:
                message = f"{message.strip()[:EXCERPT]}\n--- logged while failing ---\n{captured[-CAPTURED:]}"
            problems.append((kind, where, message.strip()[:EXCERPT + CAPTURED]))

    if not problems:
        print("::error title=No failing tests in the report::the step failed but every test passed; the failure is in the job, not the suite")
        return 0

    listed = ", ".join(where for _, where, _ in problems)
    print(f"::error title={len(problems)} failing tests::{escape(listed)}")
    for kind, where, message in problems[:LIMIT]:
        print(f"::error title={title(where)}::{kind}: {escape(message)}")
    print(f"{len(problems)} failing tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "ci-report.xml")))
