"""Finding car folders at any depth below the folders the user adds."""

import tempfile
import unittest
from pathlib import Path

import fixtures as fx
from kn5garage import carfiles


class FindCarsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, folder, **kw):
        return [p.relative_to(self.dir).as_posix() for p in carfiles.find_cars(folder, **kw)]

    def test_cars_at_every_depth(self):
        # the reported bug: a folder in a folder in the added folder was not read
        fx.make_car(self.dir / "mods", "direct")
        fx.make_car(self.dir / "mods" / "pack_one", "one_deep")
        fx.make_car(self.dir / "mods" / "pack_two" / "gt3" / "2024", "three_deep")
        (self.dir / "mods" / "empty" / "nothing" / "here").mkdir(parents=True)
        self.assertEqual(self.names(self.dir / "mods"),
                         ["mods/direct", "mods/pack_one/one_deep", "mods/pack_two/gt3/2024/three_deep"])

    def test_a_single_car_folder_is_itself(self):
        car = fx.make_car(self.dir, "solo")
        self.assertEqual(self.names(car), ["solo"])

    def test_does_not_look_inside_car_folders(self):
        car = fx.make_car(self.dir / "cars", "outer")
        fx.make_car(car / "skins" / "00_red", "not_a_car")  # a stray .kn5 inside a car's own folders
        self.assertEqual(self.names(self.dir / "cars"), ["cars/outer"])

    def test_hidden_folders_skipped(self):
        fx.make_car(self.dir / "cars" / ".backup", "old")
        fx.make_car(self.dir / "cars", "real")
        self.assertEqual(self.names(self.dir / "cars"), ["cars/real"])

    def test_depth_and_folder_limits(self):
        deep = self.dir / "a" / "b" / "c" / "d"
        fx.make_car(deep, "deep_car")
        self.assertEqual(self.names(self.dir / "a", max_depth=2), [])
        self.assertEqual(self.names(self.dir / "a", max_depth=4), ["a/b/c/d/deep_car"])
        for i in range(30):
            (self.dir / "wide" / ("f%02d" % i)).mkdir(parents=True)
        fx.make_car(self.dir / "wide" / "f29", "last")
        self.assertEqual(self.names(self.dir / "wide", max_folders=5), [])  # gave up early, no hang
        self.assertEqual(self.names(self.dir / "wide"), ["wide/f29/last"])

    def test_missing_or_unreadable_folder(self):
        self.assertEqual(carfiles.find_cars(self.dir / "gone"), [])


if __name__ == "__main__":
    unittest.main()
