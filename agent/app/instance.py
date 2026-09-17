"""One app per computer, and a second launch that finds the first.

Opening the app while it is already running is the ordinary way people get back
to it — on Windows the tray icon is hidden in the overflow by default. So a
second launch does not start a second server or fight for the port: it finds the
running one's address and opens the browser there, then exits.

"The running one" is established by a lock only this app takes, beside the
device identity. A port that happens to be busy is not evidence of anything: the
address is only trusted when the lock says this app holds it, because opening a
browser at whatever else is answering on that port is how a record gets typed
into a program that is not the record.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .. import device as device_mod

LOCK_FILENAME = "app.lock"
ADDRESS_FILENAME = "app.json"


class Instance:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or device_mod.config_home()
        self._handle = None

    @property
    def lock_path(self) -> Path:
        return self.home / LOCK_FILENAME

    @property
    def address_path(self) -> Path:
        return self.home / ADDRESS_FILENAME

    def acquire(self) -> bool:
        """Take the lock, or say another app on this computer has it."""
        if self._handle is not None:
            return True
        self.home.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            if sys.platform == "win32":  # pragma: no cover - Windows only
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def publish(self, url: str | None) -> None:
        """Say where this app is answering. Only the lock holder writes it."""
        if self._handle is None:
            return
        body = json.dumps({"pid": os.getpid(), "url": url}) + "\n"
        temporary = self.address_path.with_name(f"{ADDRESS_FILENAME}.{os.getpid()}.tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, self.address_path)

    def running_address(self) -> str | None:
        """The address the app holding the lock published, if any."""
        try:
            data: Any = json.loads(self.address_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        url = data.get("url") if isinstance(data, dict) else None
        return url if isinstance(url, str) and url.startswith("http://127.0.0.1:") else None

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        with contextlib.suppress(OSError):
            self.address_path.unlink()
        handle.close()
