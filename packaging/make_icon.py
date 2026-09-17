"""Draw the app icon for PyInstaller, which converts it to .icns and .ico."""

import sys

from agent.app.icon import save_app_icon

if __name__ == "__main__":
    save_app_icon(sys.argv[1])
