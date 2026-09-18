"""Every file this program writes holds the same bytes on every computer.

Windows opens files in text mode by default — ``os.open`` included, where the
translation happens inside ``os.write`` — so a writer that passes only bytes
still turns every ``\\n`` into ``\\r\\n`` there. Phase 3 called the newline leak
"closed by construction"; on the first machine that produces CRLF it was not,
and the event log, the wiki and every staged artefact went through it.

So this walks the package rather than trusting the one test that caught it:
a descriptor opened for writing carries ``O_BINARY``, a text-mode ``open``
states its newlines, and ``write_text`` is :func:`agent.files.write_text`
rather than ``Path``'s.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "agent"

#: Flags that mean "this descriptor is going to be written through".
WRITING = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC")

#: The one module allowed to spell the mechanism out; everything else calls it.
HOME = "agent/files.py"

#: How that module is imported where it is used.
OURS = {"files", "files_mod"}


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE.parent).as_posix()
        if "/static_files/" in rel:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8")), path


def _is_call_to(node: ast.Call, dotted: str) -> bool:
    name = dotted.split(".")[-1]
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == name:
        return True
    return isinstance(func, ast.Name) and func.id == name


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in node.keywords if kw.arg == name), None)


def _text_mode(mode: str) -> bool:
    return any(character in mode for character in "wax+") and "b" not in mode


def scan(rel: str, tree: ast.AST) -> list[str]:
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and len(node.args) > 1
        ):
            flags = ast.unparse(node.args[1])
            if any(flag in flags for flag in WRITING) and "O_BINARY" not in flags:
                problems.append(
                    f"{rel}:{node.lineno}: os.open for writing without O_BINARY — "
                    f"Windows would translate every newline: {flags}"
                )
            continue

        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
            # Our own, which encodes and writes bytes. Anything else is Path's.
            and not (isinstance(node.func.value, ast.Name) and node.func.value.id in OURS)
            and rel != HOME
        ):
            problems.append(
                f"{rel}:{node.lineno}: Path.write_text translates newlines on Windows; "
                f"use agent.files.write_text"
            )
            continue

        if isinstance(node.func, ast.Name) and node.func.id == "open" and len(node.args) > 1:
            mode = node.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and _text_mode(mode.value):
                newline = _keyword(node, "newline")
                if newline is None:
                    problems.append(
                        f"{rel}:{node.lineno}: open(..., {mode.value!r}) in text mode without "
                        f"newline=; say which newlines this file gets"
                    )
    return problems


def test_nothing_in_the_package_writes_through_text_mode():
    problems: list[str] = []
    for rel, tree, _ in _modules():
        problems.extend(scan(rel, tree))
    assert not problems, "\n  " + "\n  ".join(problems)


@pytest.mark.parametrize(
    "source",
    [
        "fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)",
        "fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)",
        "path.write_text('x', encoding='utf-8')",
        "handle = open(path, 'w', encoding='utf-8')",
        "handle = open(path, 'a', encoding='utf-8')",
    ],
)
def test_the_walker_catches_a_translating_writer(source):
    assert scan("agent/example.py", ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "fd = os.open(path, os.O_WRONLY | os.O_CREAT | O_BINARY, 0o600)",
        "fd = os.open(path, os.O_RDONLY)",
        "handle = open(path, 'rb')",
        "handle = open(path, 'wb')",
        "handle = open(path, 'w', encoding='utf-8', newline='\\n')",
        "files.write_text(path, body)",
    ],
)
def test_the_walker_lets_a_binary_writer_through(source):
    assert not scan("agent/example.py", ast.parse(source))


def test_a_written_line_is_the_same_bytes_whatever_the_platform(tmp_path):
    """The mechanism itself, rather than the absence of the old one."""
    from agent import files

    target = tmp_path / "written"
    files.write_text(target, "one\ntwo\n")

    assert target.read_bytes() == b"one\ntwo\n"
