"""No instruction in the app names a command its owner cannot run.

The downloaded app has no venv, no pip, and possibly no terminal the person
knows how to open. So a command — ``health-agent …``, ``pip install …`` — may
appear only where :mod:`agent.distribution` shows it to a pip install:

- inside ``for_terminal(...)``;
- in the ``else`` of a conditional on ``packaged()``;
- in a module constant named ``*_FOR_TERMINAL``, every use of which is itself
  inside one of the two above.

Two places are the terminal's by nature and are exempt, each with its reason.
The interface's source is walked the same way, where ``<TerminalOnly>`` is the
one place a command may be written.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from agent import distribution

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "agent"
FRONTEND = ROOT / "frontend" / "src"

COMMAND = re.compile(
    r"pip3? install"
    r"|\bhealth-agent (?=[a-z-])"
    r"|`health-agent\b"
    r"|\bnpm (?:--prefix|ci|run|install)\b"
    r"|\b(?:brew|apt|apt-get|dnf|choco|winget) install\b"
    r"|\bxattr -"
    r"|\bchmod \d"
)

#: Reached only from a terminal, so a command in them is the right instruction.
EXEMPT = {
    "agent/cli.py": "the command-line interface itself",
    "agent/demo/__init__.py": "demo vaults are seeded only by `health-agent demo`",
    "agent/demo/endpoint.py": "demo vaults are seeded only by `health-agent demo`",
}

GUARDED_SUFFIX = "_FOR_TERMINAL"


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _calls(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _is_packaged_test(node: ast.AST) -> bool:
    return _calls(node, "packaged")


def _guarded(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """Whether *node* sits where only a pip install will ever show it."""
    child = node
    parent = parents.get(id(child))
    while parent is not None:
        if _calls(parent, "for_terminal"):
            return True
        if isinstance(parent, (ast.IfExp, ast.If)) and _is_packaged_test(parent.test):
            branch = parent.orelse
            if isinstance(parent, ast.IfExp):
                if child is branch:
                    return True
            elif any(child is statement for statement in branch):
                return True
        child, parent = parent, parents.get(id(parent))
    return False


def _module_constant(node: ast.AST, parents: dict[int, ast.AST]) -> str | None:
    """The ``*_FOR_TERMINAL`` name a literal is assigned to, if it is one."""
    child = node
    parent = parents.get(id(child))
    while parent is not None and not isinstance(parent, ast.Module):
        if isinstance(parent, ast.Assign) and isinstance(parents.get(id(parent)), ast.Module):
            for target in parent.targets:
                if isinstance(target, ast.Name) and target.id.endswith(GUARDED_SUFFIX):
                    return target.id
            return None
        child, parent = parent, parents.get(id(parent))
    return None


def _python_files():
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT or "/static_files/" in rel:
            continue
        yield rel, path


def _violations_in(source: str, rel: str) -> list[str]:
    tree = ast.parse(source)
    docs = _docstrings(tree)
    parents = _parents(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.endswith(GUARDED_SUFFIX):
            if isinstance(node.ctx, ast.Load) and not _guarded(node, parents):
                found.append(f"{rel}:{node.lineno}: {node.id} used outside for_terminal()")
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docs or not COMMAND.search(node.value):
            continue
        if _guarded(node, parents) or _module_constant(node, parents):
            continue
        found.append(f"{rel}:{node.lineno}: {node.value.strip()[:100]!r}")
    return found


def test_no_command_reaches_the_app_from_python():
    found: list[str] = []
    for rel, path in _python_files():
        found.extend(_violations_in(path.read_text(encoding="utf-8"), rel))
    assert not found, (
        "A command in a message the app can show. Give it an in-app path, and put "
        "the command inside for_terminal(...):\n  " + "\n  ".join(found)
    )


_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*|\{/\*.*?\*/\}", re.DOTALL)
_TERMINAL_ONLY = re.compile(r"<TerminalOnly>.*?</TerminalOnly>", re.DOTALL)


def _interface_violations(source: str, rel: str) -> list[str]:
    stripped = _TERMINAL_ONLY.sub("", _COMMENT.sub("", source))
    return [f"{rel}: {match.group(0)!r}" for match in COMMAND.finditer(stripped)]


def test_no_command_reaches_the_app_from_the_interface():
    found: list[str] = []
    for path in sorted(FRONTEND.rglob("*.ts*")):
        rel = path.relative_to(ROOT).as_posix()
        found.extend(_interface_violations(path.read_text(encoding="utf-8"), rel))
    assert not found, (
        "A command written in the interface outside <TerminalOnly>:\n  " + "\n  ".join(found)
    )


# --- the walker catches what it is for ---------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        'message = "run `health-agent check --fix` to create it"\n',
        'def f():\n    raise ValueError(f"install it with pip install {x}")\n',
        'x = "fine" if packaged() else "fine"\ny = "health-agent probe says"\n',
        'x = "run `health-agent probe`" if packaged() else "ok"\n',
        'HINT_FOR_TERMINAL = "`health-agent ingest`"\nprint(HINT_FOR_TERMINAL)\n',
    ],
)
def test_the_walker_catches_an_unguarded_command(source):
    assert _violations_in(source, "example.py")


@pytest.mark.parametrize(
    "source",
    [
        'def f():\n    """Run `health-agent check` to see."""\n',
        'x = "ok" + for_terminal(" Run `health-agent check`.")\n',
        'x = "Settings" if packaged() else "run `health-agent set-key`"\n',
        'def f():\n    if packaged():\n        return "Settings"\n    else:\n        return "`health-agent set-key`"\n',
        'HINT_FOR_TERMINAL = "`health-agent ingest`"\nx = for_terminal(HINT_FOR_TERMINAL)\n',
        'SERVICE = "health-agent"\n',
    ],
)
def test_the_walker_lets_a_guarded_command_through(source):
    assert not _violations_in(source, "example.py")


def test_the_interface_walker_catches_and_lets_through():
    assert _interface_violations("<p>Run <code>health-agent serve</code></p>", "x.tsx")
    assert not _interface_violations(
        "<TerminalOnly>Run <code>health-agent serve</code></TerminalOnly>", "x.tsx"
    )
    assert not _interface_violations("/* health-agent serve */ <p/>", "x.tsx")


# --- and what the app actually says ------------------------------------------


@pytest.fixture
def as_the_app(monkeypatch):
    """What the frozen build sets. Patched at the source rather than on the
    function, because modules bind ``packaged`` by name when they import it."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_a_missing_bundled_library_is_a_packaging_fault_in_the_app(as_the_app):
    said = distribution.missing_library("pdfplumber", "documents", "PDFs cannot be read")
    assert "fault in how this copy was built" in said
    assert not COMMAND.search(said)
    assert not COMMAND.search(
        distribution.missing_library("keyring", "keychain", "no keychain", stored=True)
    )


def test_a_missing_library_on_pip_says_how_to_install_it():
    said = distribution.missing_library("pdfplumber", "documents", "PDFs cannot be read")
    assert "pip install 'health-agent[documents]'" in said
    stored = distribution.missing_library("pdfplumber", "documents", "x", stored=True)
    assert not COMMAND.search(stored), "stored text is read by the app on another machine"


def test_for_terminal_is_silent_in_the_app(as_the_app):
    assert distribution.for_terminal("run `health-agent check`") == ""


def test_the_missing_interface_page_names_no_command_in_the_app(as_the_app, tmp_path):
    from agent.server import static

    page = static.not_built_page(tmp_path)
    assert not COMMAND.search(page)
    assert "download it again" in page


def test_the_endpoint_messages_name_no_command():
    from agent.server import endpoint_state

    for code, message in endpoint_state.MESSAGES.items():
        assert not COMMAND.search(message), code


def test_a_missing_device_identity_names_no_command_in_the_app(as_the_app, tmp_path):
    from agent import device
    from agent.errors import DeviceIdentityError

    with pytest.raises(DeviceIdentityError) as raised:
        device.load(tmp_path / "nothing-here")
    assert not COMMAND.search(str(raised.value))
