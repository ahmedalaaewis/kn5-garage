"""Attacks on the local server: other websites (CSRF, DNS rebinding, framing), path
tricks, oversized or malformed requests, and hostile files from mods."""

import http.client
import json
import re
import unittest

from pathlib import Path

import fixtures as fx
from test_app import AppTestCase


class RawClient:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            h = {"Host": "127.0.0.1:%d" % self.port}
            h.update(headers or {})
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                conn.putheader(k, v)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            r = conn.getresponse()
            return r.status, r.read(), {k.lower(): v for k, v in r.getheaders()}
        finally:
            conn.close()


class SecurityCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.raw = RawClient(self.server.server_address[1])
        self.port = self.server.server_address[1]

    def api(self, method, path, body=None, headers=None, token=True):
        h = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            h["X-Token"] = self.app.token
        h.update(headers or {})
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        return self.raw.request(method, path, data, h)


class OtherWebsiteTests(SecurityCase):
    def test_dns_rebinding_cannot_read_the_token(self):
        # a page on evil.example that resolves to 127.0.0.1 sends its own Host header
        for host in ("evil.example:%d" % self.port, "evil.example", "127.0.0.1", "127.0.0.1.nip.io:%d" % self.port, ""):
            with self.subTest(host=host):
                status, body, _ = self.raw.request("GET", "/", headers={"Host": host})
                self.assertEqual(status, 421)
                self.assertNotIn(self.app.token.encode(), body)
                self.assertEqual(self.api("GET", "/api/settings", headers={"Host": host})[0], 421)

    def test_localhost_names_are_accepted(self):
        for host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port, "LOCALHOST:%d" % self.port):
            with self.subTest(host=host):
                self.assertEqual(self.raw.request("GET", "/", headers={"Host": host})[0], 200)

    def test_cross_site_requests_refused_even_with_token(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Origin": "http://localhost:1"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.api("POST", "/api/settings", {}, headers=headers)[0], 403)
                self.assertEqual(self.api("GET", "/api/settings", headers=headers)[0], 403)

    def test_same_origin_requests_work(self):
        for origin in ("http://127.0.0.1:%d" % self.port, "http://localhost:%d" % self.port):
            with self.subTest(origin=origin):
                status, _, _ = self.api("POST", "/api/settings", {}, headers={"Origin": origin,
                                                                              "Sec-Fetch-Site": "same-origin"})
                self.assertEqual(status, 200)

    def test_form_posts_without_json_type_refused(self):
        # an HTML form can POST cross-site without a preflight, but only as form / text types
        for ctype in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data", ""):
            with self.subTest(ctype=ctype):
                h = {"Content-Type": ctype}
                status, _, _ = self.api("POST", "/api/settings", b'{"out": "C:/x"}', headers=h)
                self.assertIn(status, (415,))
                self.assertNotEqual(self.app.settings.get("out"), "C:/x")

    def test_token_is_required_and_compared_exactly(self):
        for token in ("", "x", self.app.token[:-1], self.app.token + "x", self.app.token.upper(), "é" * 10):
            with self.subTest(token=token[:8]):
                status, _, _ = self.api("GET", "/api/settings", headers={"X-Token": token.encode("utf-8").decode("latin-1")},
                                        token=False)
                self.assertEqual(status, 403)

    def test_token_is_fresh_and_long(self):
        other = type(self.app)(settings_path=self.dir / "s2.json", default_out=self.out)
        self.assertNotEqual(other.token, self.app.token)
        self.assertGreaterEqual(len(self.app.token), 40)

    def test_page_security_headers(self):
        status, body, h = self.raw.request("GET", "/")
        self.assertEqual(status, 200)
        csp = h["content-security-policy"]
        for part in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'none'",
                     "form-action 'none'"):
            self.assertIn(part, csp)
        script_src = re.search(r"script-src ([^;]+)", csp).group(1)
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("http", script_src)  # no outside script hosts: everything is bundled
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertEqual(h["referrer-policy"], "no-referrer")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["cache-control"], "no-store")
        self.assertNotIn("python", h.get("server", "").lower())

    def test_page_loads_nothing_from_other_sites(self):
        _, body, _ = self.raw.request("GET", "/")
        html = body.decode()
        self.assertEqual(re.findall(r'(?:src|href)="(https?:)?//', html), [])
        self.assertNotIn("googleapis", html)
        self.assertNotIn("unpkg", html)
        for js in ("/web/app.js", "/web/viewer.js"):
            self.assertNotIn(b"https://", self.raw.request("GET", js)[1])

    def test_api_responses_have_security_headers(self):
        _, _, h = self.api("GET", "/api/settings")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["content-type"], "application/json")

    def test_other_methods_refused(self):
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.api(method, "/api/settings")[0], 405)


class RequestSizeTests(SecurityCase):
    def test_oversized_json_refused(self):
        big = json.dumps({"roots": ["x" * (2 << 20)]}).encode()
        self.assertEqual(self.api("POST", "/api/settings", big)[0], 413)

    def test_bad_content_length(self):
        for value in ("-1", "abc"):
            with self.subTest(value=value):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                conn.putrequest("POST", "/api/settings", skip_host=True)
                for k, v in {"Host": "127.0.0.1:%d" % self.port, "X-Token": self.app.token,
                             "Content-Type": "application/json", "Content-Length": value}.items():
                    conn.putheader(k, v)
                conn.endheaders()
                self.assertEqual(conn.getresponse().status, 400)
                conn.close()

    def test_wrong_json_types(self):
        for body in (b"[]", b'"x"', b"1", b"null", b"{bad"):
            with self.subTest(body=body):
                self.assertEqual(self.api("POST", "/api/scan", body)[0], 400)
        for body in ({"roots": "C:/"}, {"roots": [1]}, {"roots": ["x"] * 1000}):
            with self.subTest(body=str(body)[:30]):
                self.assertEqual(self.api("POST", "/api/scan", body)[0], 400)
        self.json("/api/convert", {"ids": "a_normal"}, expect=400)
        self.json("/api/convert", {"ids": [["a"]]}, expect=400)


class PathTests(SecurityCase):
    def test_static_and_output_traversal(self):
        self.scan()
        (self.dir / "secret.glb").write_bytes(b"glTF")
        self.out.mkdir(exist_ok=True)
        t = "t=" + self.app.token
        for path in ("/web/../app.py", "/web/%2e%2e/app.py", "/web/..%5c..%5capp.py", "/web/%2e%2e%2fsettings.json",
                     "/out/..%2fsecret.glb?" + t, "/out/..%5csecret.glb?" + t, "/out/%2e%2e/secret.glb?" + t,
                     "/preview/..%2f..%2fsecret.glb?" + t, "/out/C:%5cWindows%5cwin.ini?" + t,
                     "/api/file?car=a_normal&path=..%2f..%2fsecret.glb&" + t,
                     "/api/file?car=a_normal&path=C:%5cWindows%5cwin.ini&" + t,
                     "/api/file?car=a_normal&path=%5c%5c127.0.0.1%5cc$%5cx.png&" + t):
            with self.subTest(path=path):
                self.assertEqual(self.raw.request("GET", path)[0], 404)

    def test_render_names_and_blender_files_stay_in_output(self):
        png = "data:image/png;base64," + __import__("base64").b64encode(fx.png_bytes()).decode()
        for name in ("..", "../..", "C:\\Windows", "\\\\server\\share", "con", "a/../../b", "x" * 500):
            with self.subTest(name=name):
                r = self.json("/api/render", {"name": name, "index": 0, "data": png})
                target = (self.out / r["file"]).resolve()
                self.assertIn((self.out / "renders").resolve(), target.parents)
        for name in ("../settings.json", "..\\settings.json", "C:\\Windows\\notepad.glb", "/etc/x.glb"):
            with self.subTest(name=name):
                self.json("/api/blender/open", {"file": name}, expect=404)

    def test_open_only_opens_known_folders(self):
        self.json("/api/open", {"what": "renders", "name": "../../.."})
        self.assertIn((self.out / "renders").resolve(), [self.opened[-1].resolve()] + list(self.opened[-1].resolve().parents))
        self.json("/api/open", {"what": "path", "id": "C:\\Windows"}, expect=400)
        self.json("/api/open", {"what": "car", "id": "C:\\Windows"}, expect=404)

    def test_lods_ini_cannot_point_outside_the_car(self):
        car = fx.make_car(self.dir / "evil", "evil_car")
        (self.dir / "outside.kn5").write_bytes(fx.car_kn5())
        (car / "data").mkdir()
        (car / "data" / "lods.ini").write_text("[LOD_0]\nFILE=..\\..\\outside.kn5\n[LOD_1]\nFILE=../../outside.kn5\n")
        from kn5garage import carfiles
        self.assertEqual(carfiles.find_main_kn5(car).parent, car)
        lod = carfiles.find_lod_kn5(car, 1)
        self.assertTrue(lod is None or lod.parent == car)


class HostileFileTests(SecurityCase):
    def test_hostile_ui_json_is_returned_as_data(self):
        evil = '<img src=x onerror=alert(1)><script>alert(2)</script>'
        car = fx.make_car(self.dir / "evil", "xss", ui=dict(fx.UI_CAR, name=evil, author=evil, description=evil,
                                                             url="javascript:alert(1)"))
        self.assertTrue(car.is_dir())
        cars = self.json("/api/scan", {"roots": [str(self.dir / "evil")]})["cars"]
        # tags are stripped on the server, and the page only ever inserts text (web/js/dom.js)
        for key in ("name", "author"):
            self.assertNotIn("<", cars[0][key])
        d = self.json("/api/car?id=xss")
        self.assertNotIn("<", d["description"])
        self.assertEqual(d["url"], "javascript:alert(1)")  # passed as data; the page only links http(s)
        model = (Path(__file__).resolve().parent.parent / "web" / "js" / "model.js").read_text(encoding="utf-8")
        self.assertIn("https?:", model)  # safeUrl() only allows http(s) links (tests/web/model.test.mjs)

    def test_web_code_never_uses_html_injection_sinks(self):
        web = Path(__file__).resolve().parent.parent / "web"
        offenders = []
        for f in list(web.glob("*.js")) + list((web / "js").glob("*.js")):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|\beval\(|new Function\(",
                             line):
                    offenders.append("%s:%d" % (f.name, n))
        self.assertEqual(offenders, [])

    def test_huge_image_dimensions_refused(self):
        import struct
        import zlib
        from kn5garage import textures
        # a PNG header claiming 60000 x 60000 pixels
        ihdr = struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0)
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
        with self.assertRaises(Exception):
            textures.image_for_gltf(png, max_size=512)


if __name__ == "__main__":
    unittest.main()
