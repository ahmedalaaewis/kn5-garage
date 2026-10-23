"""The job queue (cancel, cleanup), caches and settings file."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import fixtures as fx
from kn5garage import cache, carfiles
from kn5garage.jobs import Jobs
from kn5garage.settings import Settings


def wait_done(jobs, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        j = jobs.get(job_id)
        if j["state"] == "done":
            return j
        time.sleep(0.01)
    raise AssertionError("job did not finish")


class JobTests(unittest.TestCase):
    def test_runs_cars_in_order_with_options(self):
        seen = []

        def run(car_id, car_dir, opts, log, progress):
            progress(0.5, "half")
            seen.append((car_id, opts["preset"]))
            return {"file": car_id + ".glb"}

        jobs = Jobs(run)
        j = wait_done(jobs, jobs.submit([("a", None), ("b", None)], {"preset": "game"}))
        self.assertEqual(seen, [("a", "game"), ("b", "game")])
        self.assertEqual([r["ok"] for r in j["results"]], [True, True])
        self.assertEqual(j["fraction"], 1.0)
        self.assertFalse(j["cancelled"])

    def test_failure_is_reported_and_next_car_still_runs(self):
        def run(car_id, car_dir, opts, log, progress):
            if car_id == "bad":
                raise ValueError("broken model")
            return {}

        jobs = Jobs(run)
        j = wait_done(jobs, jobs.submit([("bad", None), ("good", None)]))
        res = {r["id"]: r for r in j["results"]}
        self.assertEqual(res["bad"]["error"], "broken model")
        self.assertTrue(res["good"]["ok"])
        self.assertIn("  ! failed: broken model", j["log"])

    def test_cancel_stops_the_running_car_and_skips_the_rest(self):
        started = threading.Event()

        def run(car_id, car_dir, opts, log, progress):
            started.set()
            for i in range(200):  # a slow car that reports progress
                progress(i / 200, "step")
                time.sleep(0.01)
            return {}

        jobs = Jobs(run)
        job = jobs.submit([("one", None), ("two", None), ("three", None)])
        self.assertTrue(started.wait(5))
        self.assertTrue(jobs.cancel(job))
        j = wait_done(jobs, job)
        self.assertTrue(j["cancelled"])
        self.assertEqual(j["message"], "cancelled")
        self.assertEqual([r.get("cancelled") for r in j["results"]], [True, True, True])
        self.assertFalse(any(r["ok"] for r in j["results"]))
        self.assertFalse(jobs.cancel("nope"))

    def test_finished_jobs_are_pruned(self):
        jobs = Jobs(lambda *a: {})
        jobs.MAX_FINISHED = 3
        ids = [jobs.submit([("x", None)]) for _ in range(6)]
        wait_done(jobs, ids[-1])
        time.sleep(0.05)
        self.assertIsNone(jobs.get(ids[0]))
        self.assertIsNotNone(jobs.get(ids[-1]))
        self.assertLessEqual(len(jobs.jobs), 3)

    def test_get_returns_a_copy(self):
        jobs = Jobs(lambda *a: {})
        job = jobs.submit([("x", None)])
        wait_done(jobs, job)
        jobs.get(job)["results"].clear()
        self.assertEqual(len(jobs.get(job)["results"]), 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_cache_survives_restart_and_forgets_removed_cars(self):
        car = fx.make_car(self.dir / "cars", "one")
        calls = []
        make = lambda d: calls.append(d) or {"id": d.name, "n": len(calls)}
        c1 = cache.ScanCache(self.dir / "scan.json")
        self.assertEqual(c1.summary(car, make)["n"], 1)
        c1.save(keep=[car])
        c2 = cache.ScanCache(self.dir / "scan.json")
        self.assertEqual(c2.summary(car, make)["n"], 1)  # from disk
        self.assertEqual(len(calls), 1)
        c2.save(keep=[])
        self.assertEqual(json.loads((self.dir / "scan.json").read_text())["cars"], {})

    def test_broken_cache_file_is_ignored(self):
        (self.dir / "scan.json").write_text("{not json")
        c = cache.ScanCache(self.dir / "scan.json")
        self.assertEqual(c.entries, {})

    def test_fingerprint_same_for_copies_different_for_changes(self):
        a = self.dir / "a.kn5"
        a.write_bytes(b"x" * (3 << 20))
        b = self.dir / "b.kn5"
        b.write_bytes(a.read_bytes())
        c = self.dir / "c.kn5"
        c.write_bytes(b"x" * ((3 << 20) - 1) + b"y")  # last byte differs
        self.assertEqual(cache.model_fingerprint(a), cache.model_fingerprint(b))
        self.assertNotEqual(cache.model_fingerprint(a), cache.model_fingerprint(c))

    def test_preview_cache_limits(self):
        pc = cache.PreviewCache(self.dir / "prev", max_files=3)
        for i in range(5):
            p = pc.path("car__s__%d.glb" % i)
            p.write_bytes(b"x" * 10)
            import os
            os.utime(p, (i, i))
        pc.prune()
        self.assertEqual(len(list((self.dir / "prev").glob("*.glb"))), 3)
        pc.prune(keep="car__s__4.glb", prefix="car__s__")
        self.assertEqual([f.name for f in (self.dir / "prev").glob("*.glb")], ["car__s__4.glb"])

    def test_skin_stamp_changes_with_skin_files(self):
        car = fx.make_car(self.dir / "cars", "one")
        before = carfiles.skin_stamp(car, "00_red")
        f = car / "skins" / "00_red" / "body.dds"
        import os
        os.utime(f, (before + 100, before + 100))
        self.assertNotEqual(carfiles.skin_stamp(car, "00_red"), before)
        self.assertEqual(carfiles.skin_stamp(car, None), 0)


class SettingsTests(unittest.TestCase):
    def test_bad_values_in_the_file_are_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "s.json"
            p.write_text(json.dumps({"roots": "C:/x", "out": 5, "blender": ["x"]}))
            s = Settings(p, Path(t) / "out")
            self.assertEqual(s.get("roots"), [])
            self.assertEqual(s.get("out"), str(Path(t) / "out"))
            self.assertIsNone(s.get("blender"))
            p.write_text("[1, 2]")
            self.assertEqual(Settings(p, "o").get("roots"), [])

    def test_get_returns_copies_and_update_writes_atomically(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "s.json"
            s = Settings(p, "out")
            s.update(roots=["a"])
            s.get("roots").append("b")
            self.assertEqual(s.get("roots"), ["a"])
            self.assertEqual(json.loads(p.read_text())["roots"], ["a"])
            self.assertFalse((Path(t) / "s.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
