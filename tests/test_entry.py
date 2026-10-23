"""Entry points (python -m kn5garage, the .exe launcher) and where files live when packaged."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fixtures as fx
from kn5garage import __main__ as entry
from kn5garage import paths


class PathTests(unittest.TestCase):
    def test_from_source(self):
        self.assertEqual(paths.resource_dir(), paths.SOURCE_DIR)
        self.assertEqual(paths.data_dir(), paths.SOURCE_DIR)
        self.assertTrue((paths.resource_dir() / "web" / "index.html").is_file())

    def test_packaged(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", "/tmp/unpacked", create=True), \
                mock.patch.object(sys, "executable", str(Path("/apps/KN5 Garage/KN5Garage.exe"))):
            self.assertEqual(paths.resource_dir(), Path("/tmp/unpacked"))
            self.assertEqual(paths.data_dir().name, "KN5 Garage")


class EntryTests(unittest.TestCase):
    def test_convert_subcommand_runs_the_cli(self):
        with tempfile.TemporaryDirectory() as t:
            car = fx.make_car(t)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = entry.main(["convert", str(car), "-o", str(Path(t) / "o"), "--skin", "none",
                                   "--texture-size", "512", "--lights-on"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertTrue((Path(t) / "o" / "testcar.glb").is_file())

    def test_anything_else_starts_the_app(self):
        with mock.patch("kn5garage.server.main", return_value=7) as app_main:
            self.assertEqual(entry.main(["--no-browser", "--port", "1"]), 7)
        app_main.assert_called_once_with(["--no-browser", "--port", "1"])

    def test_cli_fbx_without_blender(self):
        from kn5garage import blender
        with tempfile.TemporaryDirectory() as t, mock.patch.object(blender, "candidates", lambda: []), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = entry.main(["convert", str(fx.make_car(t)), "--preset", "fbx", "-o", str(Path(t) / "o")])
        self.assertEqual(code, 1)
        self.assertIn("needs Blender", out.getvalue())


if __name__ == "__main__":
    unittest.main()
