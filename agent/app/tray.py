"""The icon in the menu bar or system tray. The only module that imports pystray.

Four items and nothing else: Open, what the reader is doing, Open the folder,
Quit. A disabled item says why in its own label — "Open — another program is
using port 7777" — rather than greying out with the reason somewhere else.
"""

from __future__ import annotations

import logging
import threading

from .icon import tray_image
from .launcher import Controller

log = logging.getLogger("agent.app")

#: How often the reader's line is re-read while the menu is closed. The menu is
#: rebuilt only when the words change.
REFRESH_S = 5.0


def run(controller: Controller) -> int:
    import pystray  # noqa: PLC0415 - optional: only the app needs it

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: controller.open_label(),
            lambda icon, item: controller.open_browser(),
            default=True,
            enabled=lambda item: controller.can_open(),
        ),
        pystray.MenuItem(lambda item: controller.status_line(), None, enabled=False),
        pystray.MenuItem(
            lambda item: controller.folder_label(),
            lambda icon, item: controller.open_folder(),
            enabled=lambda item: controller.folder() is not None,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: controller.quit()),
    )
    icon = pystray.Icon("health-record", tray_image(), "Health Record", menu)

    def redraw() -> None:
        try:
            icon.update_menu()
        except Exception:  # noqa: BLE001 - a backend without dynamic menus redraws on open
            log.debug("tray menu update not supported here", exc_info=True)

    controller.on_change = redraw

    def watch_words() -> None:
        shown = None
        while not controller.stopped.wait(REFRESH_S):
            words = (controller.open_label(), controller.status_line(), controller.folder_label())
            if words != shown:
                shown = words
                redraw()

    def watch_stopped() -> None:
        controller.wait()
        icon.stop()

    def setup(started) -> None:
        started.visible = True
        controller.start()
        threading.Thread(target=watch_words, name="health-agent-tray-words", daemon=True).start()
        threading.Thread(target=watch_stopped, name="health-agent-tray-stop", daemon=True).start()

    icon.run(setup=setup)
    # The tray loop can end without Quit — a logout on some desktops. The
    # record's shutdown still has to happen before the process goes.
    controller.quit()
    controller.wait(timeout=90)
    return 0
