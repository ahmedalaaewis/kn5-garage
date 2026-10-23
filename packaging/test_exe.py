"""Check a built KN5Garage.exe from a clean folder: the app starts, serves its page, scans
and exports a car, and the `convert` command works. Used by build checks and the release workflow.

    python packaging/test_exe.py dist/KN5Garage.exe
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import fixtures as fx  # noqa: E402
import glb_check  # noqa: E402


def stop(proc):
    """A one-file exe runs as two processes (unpacker + app): stop the whole tree."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def check_app(local, cars, app_dir, cwd):
    proc = subprocess.Popen([str(local), "--port", "0", "--no-browser"], cwd=str(cwd),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = proc.stdout.readline()
        m = re.search(r"http://127\.0\.0\.1:\d+/", line)
        assert m, "app did not start: %r" % line
        base = m.group(0).rstrip("/")
        page = urllib.request.urlopen(base + "/", timeout=30).read().decode()
        token = re.search(r'name="token" content="([^"]+)"', page).group(1)
        three = urllib.request.urlopen(base + "/web/vendor/three/three.module.js", timeout=30).read()
        assert len(three) > 1_000_000, "bundled three.js missing"

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-Token": token, "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=60).read())

        found = {c["id"] for c in call("/api/scan", {"roots": [str(cars)]})["cars"]}
        assert found == {"a_normal", "b_protected", "c_encrypted", "d_broken_json"}, found
        job = call("/api/convert", {"ids": ["a_normal"]})["job"]
        for _ in range(300):
            j = call("/api/job?id=" + job)
            if j["state"] == "done":
                break
            time.sleep(0.1)
        res = j["results"][0]
        assert res["ok"], res
        out = app_dir / "output" / res["file"]
        assert out.is_file(), "export not next to the exe: %s" % out
        assert not glb_check.load(out).problems
        assert (app_dir / "settings.json").is_file(), "settings not next to the exe"
    finally:
        stop(proc)


def check_cli(local, cars, tmp):
    p = subprocess.run([str(local), "convert", str(cars / "a_normal"), "-o", str(tmp / "cli"), "--preset", "obj"],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    assert list((tmp / "cli").glob("*.obj")), "convert wrote no .obj"


def main(exe):
    exe = Path(exe).resolve()
    tmp = Path(tempfile.mkdtemp())
    try:
        app_dir = tmp / "KN5 Garage"
        app_dir.mkdir()
        local = app_dir / "KN5Garage.exe"
        shutil.copy2(exe, local)
        cars = fx.make_garage(tmp / "cars")
        check_app(local, cars, app_dir, tmp)
        check_cli(local, cars, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("KN5Garage.exe OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist" / "KN5Garage.exe"))
