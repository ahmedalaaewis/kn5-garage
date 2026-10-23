"""
End-to-end smoke test: runs the real programs the way a user would.

    python tests/smoke_test.py                       -> synthetic cars only
    python tests/smoke_test.py "D:\\path\\to\\car" ...  -> also your real car folders

Checks:
  1. the command-line converter on a cars folder (normal, protected, encrypted, broken)
  2. the KN5 Garage app, started as its own process: page, scan, details, export, download,
     export presets (game with paint, print to STL), the 3D preview and saving renders
  3. every .glb (and .stl) produced passes a strict validator
  4. no file in any car folder was changed, added or removed (read-only guarantee)
Real car folders are only read; all output goes to a temporary folder.
"""

import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import fixtures as fx  # noqa: E402
import glb_check  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail and not ok else ""))
    return ok


def fingerprint(folder):
    """path -> (size, mtime, sha256) for every file under folder."""
    out = {}
    for f in sorted(Path(folder).rglob("*")):
        if f.is_file():
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            st = f.stat()
            out[str(f.relative_to(folder))] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return out


def validate_glb(path):
    try:
        g = glb_check.load(path)
    except Exception as e:
        return False, str(e)
    if g.problems:
        return False, "; ".join(g.problems[:5])
    if not g.json.get("meshes"):
        return False, "no meshes"
    return True, "%d meshes, %d images" % (len(g.json["meshes"]), len(g.json.get("images", [])))


def validate_stl(data):
    if len(data) < 84:
        return False, "file too small"
    n = int.from_bytes(data[80:84], "little")
    if len(data) != 84 + 50 * n or n == 0:
        return False, "size %d doesn't match %d triangles" % (len(data), n)
    return True, "%d triangles" % n


def run_cli(folder, out):
    p = subprocess.run([sys.executable, str(ROOT / "kn5_to_glb.py"), str(folder), "-o", str(out)],
                       capture_output=True, text=True, timeout=1800)
    return p.returncode, p.stdout + p.stderr


class AppProcess:
    def __init__(self, tmp):
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "app.py"), "--port", "0", "--no-browser",
             "--settings", str(tmp / "settings.json"), "--out", str(tmp / "app_out")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = self.proc.stdout.readline()
        m = re.search(r"http://127\.0\.0\.1:\d+/", line)
        if not m:
            self.proc.kill()
            raise RuntimeError("app did not start: %r" % line)
        self.base = m.group(0).rstrip("/")
        html = self.get("/", raw=True).decode()
        self.token = re.search(r'name="token" content="([^"]+)"', html).group(1)

    def get(self, path, raw=False, body=None):
        headers = {"X-Token": getattr(self, "token", "")}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
        return payload if raw else json.loads(payload)

    def wait(self, job, timeout=1800):
        end = time.time() + timeout
        while time.time() < end:
            j = self.get("/api/job?id=" + job)
            if j["state"] == "done":
                return j
            time.sleep(0.2)
        raise TimeoutError("job %s did not finish" % job)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def smoke_cars_folder(label, cars_root, expect, tmp):
    """expect: car id -> 'ok' | 'protected' | 'fail'"""
    print("\n== %s: %s" % (label, cars_root))
    before = fingerprint(cars_root)

    print(" command line")
    out = tmp / ("cli_" + label)
    t = time.time()
    code, text = run_cli(cars_root, out)
    all_ok = all(v != "fail" for v in expect.values())
    check("%s: CLI exit code" % label, code == (0 if all_ok else 1), "got %d\n%s" % (code, text[-800:]))
    made = {p.name: p for p in out.glob("*.glb")}
    for car_id, kind in expect.items():
        hits = [p for n, p in made.items() if n == car_id + ".glb" or n.startswith(car_id + "__")]
        if kind == "fail":
            check("%s/%s: CLI reports failure" % (label, car_id), not hits and "failed" in text)
            continue
        if check("%s/%s: CLI wrote a .glb" % (label, car_id), hits, text[-500:]):
            ok, detail = validate_glb(hits[0])
            check("%s/%s: .glb is valid (%s)" % (label, car_id, detail), ok, detail)
    if any(v == "protected" for v in expect.values()):
        check("%s: CLI warns about protected cars" % label, "protected by its author" in text)
    print("  (%.1fs)" % (time.time() - t))

    print(" app")
    app = AppProcess(tmp / ("app_" + label))
    try:
        page = app.get("/", raw=True)
        check("%s: app page served" % label, b"KN5" in page and b"__TOKEN__" not in page)
        try:
            urllib.request.urlopen(app.base + "/api/settings", timeout=10)
            check("%s: API rejects calls without token" % label, False)
        except urllib.error.HTTPError as e:
            check("%s: API rejects calls without token" % label, e.code == 403)
        settings = app.get("/api/settings")
        check("%s: settings list folders and presets" % label,
              settings["roots"] == [] and {"render", "game", "print", "obj"} <= set(settings["presets"]), str(settings))
        cars = {c["id"]: c for c in app.get("/api/scan", body={"roots": [str(cars_root)]})["cars"]}
        check("%s: scan found every car" % label, set(cars) == set(expect), "found %s" % sorted(cars))
        check("%s: scan remembers the folder" % label, app.get("/api/settings")["roots"] == [str(cars_root)])
        check("%s: cars carry folder, duplicate and issue info" % label,
              all({"root", "folder", "duplicates", "issues", "hasLod", "hasAnimations"} <= set(c) for c in cars.values()))
        for car_id, kind in expect.items():
            c = cars.get(car_id)
            if not c:
                continue
            want = {"ok": "ok", "protected": "protected", "fail": ("encrypted", "no-model")}[kind]
            check("%s/%s: status %s" % (label, car_id, c["status"]),
                  c["status"] in (want if isinstance(want, tuple) else (want,)))
            d = app.get("/api/car?id=" + urllib.request.quote(car_id))
            check("%s/%s: details load (author: %s)" % (label, car_id, d.get("author")), d["id"] == car_id)
            if d.get("preview"):
                img = app.get("/api/file?car=%s&path=%s" % (urllib.request.quote(car_id),
                                                            urllib.request.quote(d["preview"])), raw=True)
                check("%s/%s: preview image served" % (label, car_id), len(img) > 0)
        job = app.get("/api/convert", body={"ids": list(expect)})["job"]
        j = app.wait(job)
        res = {r["id"]: r for r in j["results"]}
        for car_id, kind in expect.items():
            r = res.get(car_id, {})
            if kind == "fail":
                check("%s/%s: app export fails cleanly" % (label, car_id), r and not r["ok"] and r.get("error"))
                continue
            if not check("%s/%s: app export ok" % (label, car_id), r.get("ok"), r.get("error", "")):
                continue
            check("%s/%s: protected flag" % (label, car_id), r["protected"] == (kind == "protected"))
            check("%s/%s: export report" % (label, car_id),
                  r.get("report") and all(i["level"] in ("ok", "info", "warn") for i in r["report"]), str(r.get("report")))
            check("%s/%s: Blender setup script written" % (label, car_id),
                  r.get("script") and (tmp / ("app_" + label) / "app_out" / r["script"]).is_file())
            data = app.get("/out/" + urllib.request.quote(r["file"]) + "?t=" + app.token, raw=True)
            try:
                g = glb_check.Glb(data)
                check("%s/%s: downloaded .glb valid" % (label, car_id), not g.problems, "; ".join(g.problems[:3]))
            except Exception as e:
                check("%s/%s: downloaded .glb valid" % (label, car_id), False, str(e))
        smoke_app_extras(app, label, [c for c, kind in expect.items() if kind != "fail"], tmp / ("app_" + label) / "app_out")
    finally:
        app.stop()

    after = fingerprint(cars_root)
    changed = [p for p in set(before) | set(after) if before.get(p) != after.get(p)]
    check("%s: car folders untouched (%d files checked)" % (label, len(before)), not changed,
          "changed: %s" % changed[:5])


def smoke_app_extras(app, label, ids, out):
    """Presets, paint, 3D preview and saving renders, on the first exportable car."""
    if not ids:
        return
    car_id = ids[0]
    q = urllib.request.quote(car_id)
    j = app.wait(app.get("/api/convert", body={"ids": [car_id], "preset": "game", "paint": "#1e90ff",
                                               "paintMode": "tint"})["job"])
    r = j["results"][0]
    if check("%s/%s: game preset with paint" % (label, car_id), r.get("ok") and r.get("preset") == "game",
             r.get("error", "")):
        ok, detail = validate_glb(out / r["file"])
        check("%s/%s: game .glb valid (%s)" % (label, car_id, detail), ok, detail)
    j = app.wait(app.get("/api/convert", body={"ids": [car_id], "preset": "print"})["job"])
    r = j["results"][0]
    if check("%s/%s: print preset" % (label, car_id), r.get("ok") and r.get("file", "").endswith(".stl"),
             r.get("error", "")):
        ok, detail = validate_stl((out / r["file"]).read_bytes())
        check("%s/%s: .stl valid (%s)" % (label, car_id, detail), ok, detail)
        listed = {f["name"]: f for f in app.get("/api/outputs")["files"]}
        check("%s/%s: .stl listed, not viewable" % (label, car_id), listed.get(r["file"], {}).get("viewable") is False)

    try:
        url = app.get("/api/preview?id=%s" % q)["url"]
        data = app.get(url + "?t=" + app.token, raw=True)
        g = glb_check.Glb(data)
        check("%s/%s: 3D preview valid" % (label, car_id), not g.problems, "; ".join(g.problems[:3]))
    except Exception as e:
        check("%s/%s: 3D preview valid" % (label, car_id), False, str(e))

    png = "data:image/png;base64," + base64.b64encode(fx.png_bytes()).decode()
    saved = app.get("/api/render", body={"name": car_id, "index": 0, "data": png})
    check("%s/%s: render frame saved" % (label, car_id), (out / saved["file"]).is_file())
    b = app.get("/api/blender")
    check("%s: Blender lookup answers (found: %s)" % (label, b["found"]), "found" in b)


def main(real_cars):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        garage = fx.make_garage(tmp / "cars")
        smoke_cars_folder("synthetic", garage, {"a_normal": "ok", "b_protected": "protected",
                                                "c_encrypted": "fail", "d_broken_json": "ok"}, tmp)
        for i, car in enumerate(real_cars):
            car = Path(car)
            if not car.is_dir():
                check("real car folder exists: %s" % car, False)
                continue
            import kn5_to_glb as k
            probe = k.probe_kn5(k.find_main_kn5(car)) if k.find_main_kn5(car) else {"readable": False}
            kind = "fail" if not probe["readable"] else "protected" if probe["protected"] else "ok"
            smoke_cars_folder("real%d" % (i + 1), car, {car.name: kind}, tmp)

    failed = [r for r in results if not r[1]]
    print("\n%d checks, %d passed, %d failed" % (len(results), len(results) - len(failed), len(failed)))
    for name, _, detail in failed:
        print("  FAILED: %s %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
