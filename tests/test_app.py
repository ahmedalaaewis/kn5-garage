import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import fixtures as fx
import glb_check
import app as garage


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cars = fx.make_garage(self.dir / "cars")
        self.out = self.dir / "out"
        self.picked = []
        self.opened = []
        self.app = garage.App(settings_path=self.dir / "settings.json", default_out=self.out,
                              pick_folder=self.fake_pick, open_path=self.opened.append)
        self.server = garage.make_server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def fake_pick(self, title):
        self.picked.append(title)
        return str(self.cars)

    def request(self, path, body=None, token=True, raw=None):
        headers = {"X-Token": self.app.token} if token else {}
        data = None
        if body is not None or raw is not None:
            data = raw if raw is not None else json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def json(self, path, body=None, expect=200, **kw):
        status, data, _ = self.request(path, body, **kw)
        self.assertEqual(status, expect, data)
        return json.loads(data)

    def scan(self):
        return self.json("/api/scan", {"root": str(self.cars)})

    def wait_job(self, job, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            j = self.json("/api/job?id=" + job)
            if j["state"] == "done":
                return j
            time.sleep(0.05)
        self.fail("job did not finish")


class PageTests(AppTestCase):
    def test_index_has_token_and_static_files_load(self):
        status, html, headers = self.request("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), html)
        self.assertNotIn(b"__TOKEN__", html)
        for f in ("/web/app.js", "/web/style.css", "/web/viewer.js"):
            self.assertEqual(self.request(f, token=False)[0], 200, f)

    def test_static_traversal_blocked(self):
        for path in ("/web/../app.py", "/web/%2e%2e/app.py", "/web/..%5capp.py", "/web/nope.js"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, token=False)[0], 404)


class AuthTests(AppTestCase):
    def test_api_needs_token(self):
        self.assertEqual(self.request("/api/settings", token=False)[0], 403)
        self.assertEqual(self.request("/api/scan", {"root": str(self.cars)}, token=False)[0], 403)
        status, _, _ = self.request("/api/settings?t=wrong", token=False)
        self.assertEqual(status, 403)

    def test_query_token_only_works_for_loading_files(self):
        self.scan()
        q = "/api/file?car=a_normal&path=skins/00_red/preview.jpg&t=" + self.app.token
        self.assertEqual(self.request(q, token=False)[0], 200)
        status, _, _ = self.request("/api/settings?t=" + self.app.token, token=False)
        self.assertEqual(status, 403)

    def test_server_only_listens_locally(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class ApiTests(AppTestCase):
    def test_settings_roundtrip_and_persist(self):
        s = self.json("/api/settings")
        self.assertEqual(Path(s["out"]), self.out)
        self.json("/api/settings", {"out": str(self.dir / "elsewhere")})
        saved = json.loads((self.dir / "settings.json").read_text())
        self.assertEqual(Path(saved["out"]), self.dir / "elsewhere")
        self.json("/api/settings", {"out": "  "}, expect=400)

    def test_scan_lists_cars_with_status(self):
        r = self.scan()
        status = {c["id"]: c["status"] for c in r["cars"]}
        self.assertEqual(status, {"a_normal": "ok", "b_protected": "protected",
                                  "c_encrypted": "encrypted", "d_broken_json": "ok"})
        self.assertEqual(json.loads((self.dir / "settings.json").read_text())["roots"], [str(self.cars)])

    def test_scan_bad_folder(self):
        self.json("/api/scan", {"root": str(self.dir / "nope")}, expect=400)

    def test_car_details_need_scan_and_work_after(self):
        self.json("/api/car?id=a_normal", expect=404)
        self.scan()
        d = self.json("/api/car?id=a_normal")
        self.assertEqual(d["author"], "Jane Modder")
        self.assertEqual(len(d["skins"]), 2)

    def test_file_endpoint_serves_images_only_inside_car(self):
        self.scan()
        q = "/api/file?car=a_normal&path="
        status, data, headers = self.request(q + "skins/00_red/preview.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        for bad in ("../b_protected/ui/badge.png", "a_normal.kn5", "ui/ui_car.json"):
            with self.subTest(path=bad):
                self.assertEqual(self.request(q + bad)[0], 404)

    def test_convert_single_car_with_skin(self):
        self.scan()
        job = self.json("/api/convert", {"ids": ["a_normal"], "skin": "01_blue"})["job"]
        j = self.wait_job(job)
        self.assertEqual(j["done"], 1)
        self.assertAlmostEqual(j["fraction"], 1.0)
        res = j["results"][0]
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["file"], "a_normal__01_blue.glb")
        self.assertFalse(res["protected"])
        status, data, headers = self.request("/out/" + res["file"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "model/gltf-binary")
        self.assertEqual(glb_check.Glb(data).problems, [])
        files = self.json("/api/outputs")["files"]
        self.assertEqual([f["name"] for f in files], ["a_normal__01_blue.glb"])

    def test_batch_convert_reports_each_car(self):
        self.scan()
        ids = ["a_normal", "b_protected", "c_encrypted"]
        j = self.wait_job(self.json("/api/convert", {"ids": ids, "skin": "01_blue"})["job"])
        res = {r["id"]: r for r in j["results"]}
        self.assertTrue(res["a_normal"]["ok"])
        self.assertEqual(res["a_normal"]["skin"], "00_red")  # skin choice only applies to one car
        self.assertTrue(res["b_protected"]["ok"])
        self.assertTrue(res["b_protected"]["protected"])
        self.assertFalse(res["c_encrypted"]["ok"])
        self.assertIn("encrypted", res["c_encrypted"]["error"])
        self.assertTrue(any("protected by its author" in line for line in j["log"]))

    def test_convert_errors(self):
        self.json("/api/convert", {"ids": []}, expect=400)
        self.json("/api/convert", {"ids": ["ghost"]}, expect=404)
        self.assertEqual(self.request("/api/convert", raw=b"{not json")[0], 400)
        self.assertEqual(self.request("/api/convert", raw=b"[1]")[0], 400)
        self.json("/api/job?id=nope", expect=404)

    def test_out_endpoint_only_serves_glb_files(self):
        self.out.mkdir()
        (self.out / "notes.txt").write_text("x")
        (self.dir / "outside.glb").write_bytes(b"glTF")
        for bad in ("notes.txt", "..%2foutside.glb", "missing.glb"):
            with self.subTest(name=bad):
                self.assertEqual(self.request("/out/" + bad)[0], 404)

    def test_pick_and_open_use_injected_helpers(self):
        r = self.json("/api/pick", {"what": "root"})
        self.assertEqual(r["path"], str(self.cars))
        self.assertEqual(len(self.picked), 1)
        self.json("/api/pick", {"what": "bogus"}, expect=400)
        self.json("/api/open", {"what": "out"})
        self.assertEqual(self.opened, [self.out])
        self.scan()
        self.json("/api/open", {"what": "car", "id": "a_normal"})
        self.assertEqual(self.opened[-1], self.cars / "a_normal")
        self.json("/api/open", {"what": "anything"}, expect=400)

    def test_unknown_route(self):
        self.json("/api/nothing", expect=404)
        self.json("/api/nothing", {}, expect=404)


class RemovedFeatureTests(AppTestCase):
    def test_builder_endpoints_are_gone(self):
        for path in ("/api/bp/state", "/api/bp/file?view=side", "/api/presets"):
            self.assertEqual(self.request(path)[0], 404, path)
        for path in ("/api/bp/build", "/api/bp/image", "/api/generate"):
            self.assertEqual(self.request(path, {})[0], 404, path)
        self.assertEqual(self.request("/web/builder.js", token=False)[0], 404)


if __name__ == "__main__":
    unittest.main()
