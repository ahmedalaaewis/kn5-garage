"""Entry point for `python -m kn5garage` and for KN5Garage.exe.

    KN5Garage                      -> starts the app and opens it in your browser
    KN5Garage --help               -> app options
    KN5Garage convert <folder> ... -> command-line converter (see `KN5Garage convert --help`)
"""

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["convert"]:
        from .cli import main as cli_main
        return cli_main(argv[1:])
    from .server import main as app_main
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
