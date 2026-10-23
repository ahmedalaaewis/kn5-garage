"""Where the app's files live, both when run from source and as the single KN5Garage.exe.

resource_dir(): read-only files shipped with the app (the web page). Inside the .exe they are
                unpacked to a temporary folder by PyInstaller (sys._MEIPASS).
data_dir():     where settings.json, library.json and the default output folder go: the project
                folder when run from source, the folder holding KN5Garage.exe when packaged.
"""

import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent.parent


def frozen():
    return bool(getattr(sys, "frozen", False))


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", SOURCE_DIR)) if frozen() else SOURCE_DIR


def data_dir():
    return Path(sys.executable).resolve().parent if frozen() else SOURCE_DIR
