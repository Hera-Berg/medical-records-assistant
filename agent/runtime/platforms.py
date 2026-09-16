"""This machine: which build it needs, where files go, what it has room for.

Files live **outside the vault**. The vault syncs to Dropbox, Drive or
Nextcloud, and 3.6 GB of model weights inside it would be uploaded to a third
party on every machine that shares the folder — and then downloaded again to
every other one. :func:`data_home` follows each platform's own convention for
application data, and :mod:`.store` refuses a location that resolves inside the
vault however it got there.

Memory and disk are read with the standard library and ``ctypes``. They are
consulted twice — before a download, and on the settings screen — and a
dependency for two numbers would be a poor trade.
"""

from __future__ import annotations

import ctypes
import os
import platform as platform_mod
import shutil
import subprocess
import sys
from pathlib import Path

ENV_DATA_HOME = "HEALTH_AGENT_DATA_HOME"

LINUX_X64 = "linux-x64"
MACOS_ARM64 = "macos-arm64"
MACOS_X64 = "macos-x64"
WINDOWS_X64 = "windows-x64"

ALL = (LINUX_X64, MACOS_ARM64, MACOS_X64, WINDOWS_X64)

#: Platforms whose download, start, sleep and stop have actually been run
#: end to end during development. The others are pinned and **unverified**, and
#: the settings screen says so rather than letting code that exists for a
#: platform read as a claim that it works there.
VERIFIED = frozenset({LINUX_X64})

LABELS = {
    LINUX_X64: "Linux (x86-64)",
    MACOS_ARM64: "macOS (Apple Silicon)",
    MACOS_X64: "macOS (Intel)",
    WINDOWS_X64: "Windows (x86-64)",
}

#: Below this much physical memory the reader still runs, slowly and at the
#: expense of everything else open. A warning, never a refusal.
LOW_MEMORY_BYTES = 8 * 1024**3


def current(system: str | None = None, machine: str | None = None) -> str | None:
    """This machine's platform key, or ``None`` where no build is pinned."""
    system = (system or platform_mod.system()).lower()
    machine = (machine or platform_mod.machine()).lower()
    x64 = machine in ("x86_64", "amd64", "x64")
    arm64 = machine in ("arm64", "aarch64")
    if system == "linux" and x64:
        return LINUX_X64
    if system == "darwin" and arm64:
        return MACOS_ARM64
    if system == "darwin" and x64:
        return MACOS_X64
    if system == "windows" and x64:
        return WINDOWS_X64
    return None


def data_home() -> Path:
    """Where the reader's binary, weights and log live. Never inside a vault."""
    override = os.environ.get(ENV_DATA_HOME)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "health-agent"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "health-agent"
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "health-agent"


def is_inside(path: Path, root: Path) -> bool:
    """Whether *path* resolves to *root* or somewhere beneath it."""
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def total_memory_bytes() -> int | None:
    """Physical memory, or ``None`` if this machine will not say."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
            return None
        if sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return int(result.stdout.strip()) if result.returncode == 0 else None
        if sys.platform == "win32":
            return _windows_memory()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _windows_memory() -> int | None:  # pragma: no cover - Windows only
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return int(status.ullTotalPhys)


def free_disk_bytes(path: Path) -> int | None:
    """Free space on the filesystem holding *path*, or its nearest existing parent."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None
