"""What KN5Garage.exe runs (PyInstaller entry point)."""

import sys

from kn5garage.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
