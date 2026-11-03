"""Running the interface's plain JavaScript inside a test, on every platform.

Node is installed on every CI runner; Apple's JavaScriptCore exists only on a
Mac. Checks that looked for JavaScriptCore alone skipped themselves on Linux and
Windows, and a skipped check reads exactly like one that passed. So Node is
preferred, JavaScriptCore is the fallback, and a script is always run from a
file: passed with ``-e``, the whole bundle overflows the argument list on Linux.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "agent/server/static_files"
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")

#: JavaScriptCore has ``print`` and Node has ``console.log``; scripts use ``print``.
PRELUDE = "if (typeof globalThis.print !== 'function') globalThis.print = console.log;\n"


def runtime() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    return str(JSC) if JSC.is_file() else None


def run(script: str, directory: Path, name: str = "check.js", timeout: int = 30):
    """Run *script* with whichever runtime is here. Callers skip when there is none."""
    path = Path(directory) / name
    path.write_text(PRELUDE + script, encoding="utf-8")
    return subprocess.run(
        [runtime(), str(path)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout
    )


def bundle_path(index_html: str | None = None) -> str:
    """The application bundle ``index.html`` loads — never whichever file a glob lists first."""
    index = index_html if index_html is not None else (STATIC / "index.html").read_text(encoding="utf-8")
    return re.search(r'src="(/assets/[^"]+\.js)"', index).group(1)


def shipped_bundle() -> str:
    return (STATIC / bundle_path().lstrip("/")).read_text(encoding="utf-8")


def routes_to(bundle: str, segment: str, element: str) -> bool:
    """Whether the bundle renders custom element *element* for path *segment*.

    Matched on shape, not on minified names: a real build renames every
    identifier, and the JSX runtime may emit ``jsx`` or ``createElement``. The
    element may sit inside a wrapper (a fragment beside a banner) in the same
    branch, so anything without a ``?`` — which would start another branch —
    may come between the condition and the element.
    """
    pattern = (
        rf"===`{re.escape(segment)}`\?[^?]{{0,400}}?"
        rf"\(0,[\w$]+\.(?:jsx|createElement)\)\(`{re.escape(element)}`"
    )
    return re.search(pattern, bundle) is not None


def function_source(bundle: str, start: int) -> str:
    """The whole ``function …{…}`` beginning at *start*, braces matched.

    Cutting at the next ``function`` keyword stops at the first nested
    function, so the body is scanned instead, skipping quoted strings and
    template literals (including the code inside ``${…}``).
    """
    open_brace = bundle.index("{", bundle.index(")", start))
    stack = ["code"]  # "code" frames count braces; "tpl" frames are template text
    depth = [0]
    i = open_brace
    while i < len(bundle):
        c = bundle[i]
        if stack[-1] == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
                depth.pop()
            elif c == "$" and bundle.startswith("${", i):
                stack.append("code")
                depth.append(0)
                i += 2
                continue
        elif c in "'\"":
            i += 1
            while bundle[i] != c:
                i += 2 if bundle[i] == "\\" else 1
        elif c == "`":
            stack.append("tpl")
            depth.append(0)
        elif c == "{":
            depth[-1] += 1
        elif c == "}":
            if depth[-1] == 0 and len(stack) > 1:  # closes a ${…}
                stack.pop()
                depth.pop()
            else:
                depth[-1] -= 1
                if len(stack) == 1 and depth[0] == 0:
                    return bundle[start : i + 1]
        i += 1
    raise ValueError("unbalanced function body")
