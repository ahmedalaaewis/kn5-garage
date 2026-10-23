"""
KN5 Garage - a local web UI for converting Assetto Corsa cars.

    python app.py            -> starts the app and opens it in your browser
    python app.py --help     -> options

The server only listens on 127.0.0.1 (this PC); see SECURITY.md for how it is protected.
The code lives in the kn5garage package (kn5garage/server.py).
"""

import sys

from kn5garage.server import App, ApiError, Handler, Settings, main, make_server  # noqa: F401

if __name__ == "__main__":
    sys.exit(main())
