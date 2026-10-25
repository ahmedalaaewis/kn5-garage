"""
Make the README screenshots (docs/screenshots/*.png) from the demo garage.

    python tools/screenshots.py

It builds the demo garage (tools/make_demo_garage.py), runs the app on temporary settings,
renders each demo car's preview pictures with the real 3D viewer, and photographs the app
through deep links (see web/app.js) with headless Edge or Chrome. A throwaway browser profile
is used, so your own browser profile, settings and car folders are never touched.
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

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import make_demo_garage as demo  # noqa: E402

BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/microsoft-edge"]

SHOTS = [  # file, deep link, window size
    ("library", "car=lum_aurora_spyder", (1600, 1000)),
    ("viewer-night", "car=vel_corsa_gt3&viewer={export}&studio=night&lights=1&steer=22&spin=0", (1600, 900)),
    ("viewer-day", "car=hos_sakura_drift&viewer=preview&studio=day&steer=-25&spin=0", (1600, 900)),
    ("viewer-wire", "car=tem_tempest_lmp&viewer=preview&studio=studio&mode=texwire&spin=0", (1600, 900)),
    ("table", "layout=table&car=kes_nordlicht_rs", (1600, 1000)),
    ("compare", "select=vel_corsa_gt3,lum_aurora_spyder,tem_tempest_lmp,kes_nordlicht_rs&open=compare", (1600, 1000)),
    ("export", "car=vel_corsa_gt3&open=export&preset=game", (1600, 1000)),
    ("edit", "layout=cards&car=kes_nordlicht_rs&tab=edit", (1600, 1700)),
    ("parts-editor", "car=vel_corsa_gt3&skin=red_17&open=parts&spin=0", (1600, 900)),
    ("livery", "car=lum_aurora_spyder&open=livery&spin=0", (1600, 900)),
    ("import", "car=fjo_bluebird_rally&open=import", (1600, 1000)),
    ("character", "car=walker&viewer=preview&bones=1&spin=0", (1600, 900)),
    ("print", "car=tower&open=print", (1600, 1000)),
    ("disk-space", "open=space", (1600, 1000)),
    ("skin-gallery", "open=skins", (1600, 1000)),
    ("italiano", "lang=it&car=fjo_bluebird_rally&tab=skins", (1600, 1000)),
    ("arabic", "lang=ar&car=tem_tempest_lmp", (1600, 1000)),
    ("japanese", "lang=ja&car=hos_sakura_drift&viewer=preview&studio=day&steer=-20&spin=0", (1600, 900)),
]


def find_browser():
    for b in BROWSERS:
        if Path(b).is_file():
            return b
    sys.exit("No Edge or Chrome found for the headless screenshots.")


class App:
    def __init__(self, tmp, cars):
        (tmp / "settings.json").write_text(json.dumps({"roots": [str(cars)]}), encoding="utf-8")
        out = tmp / "KN5 Garage" / "output"  # what the Output pill shows (the end of the path)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "app.py"), "--port", "0", "--no-browser",
                                      "--settings", str(tmp / "settings.json"), "--out", str(out)],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.base = re.search(r"http://127\.0\.0\.1:\d+", self.proc.stdout.readline()).group(0)
        self.token = re.search(r'name="token" content="([^"]+)"', self.get("/", raw=True).decode()).group(1)

    def get(self, path, body=None, raw=False):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Token": getattr(self, "token", ""), "Content-Type": "application/json"})
        data = urllib.request.urlopen(req, timeout=120).read()
        return data if raw else json.loads(data)

    def export(self, ids, **options):
        job = self.get("/api/convert", dict(ids=ids, **options))["job"]
        while True:
            j = self.get("/api/job?id=" + job)
            if j["state"] == "done":
                return j["results"]
            time.sleep(0.2)

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)


def shoot(browser, profile, url, out, size):
    subprocess.run([browser, "--headless=new", "--user-data-dir=%s" % profile, "--no-first-run", "--disable-extensions",
                    "--disable-sync", "--no-default-browser-check", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--window-size=%d,%d" % size, "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                    "--virtual-time-budget=25000", "--screenshot=%s" % out, url],
                   check=True, timeout=180, capture_output=True)


def main():
    browser = find_browser()
    tmp = Path(tempfile.mkdtemp(prefix="kn5shots_"))
    cars = demo.main([str(tmp / "cars")])
    app = App(tmp, cars)
    profile = tmp / "browser-profile"
    try:
        app.get("/api/scan", {"roots": [str(cars)]})
        # 1. real 3D pictures for every demo skin (replacing the placeholders)
        for spec in demo.CARS:
            for skin_id, *_ in spec["skins"]:
                png = tmp / "preview.png"
                shoot(browser, profile, "%s/#car=%s&skin=%s&viewer=preview&spin=0" % (app.base, spec["id"], skin_id),
                      png, (1600, 900))
                img = Image.open(png).convert("RGB").crop((260, 250, 1340, 857)).resize((640, 360), Image.LANCZOS)
                for car in (cars / spec["id"], cars / (spec["id"] + "_old_copy")):
                    if (car / "skins" / skin_id).is_dir():
                        img.save(car / "skins" / skin_id / "preview.jpg", quality=88)
                print("preview", spec["id"], skin_id)
        app.get("/api/scan", {"roots": [str(cars)]})
        # 2. a lived-in library: favourites, tags, exports in the history
        app.get("/api/carmeta", {"id": "lum_aurora_spyder", "favourite": True, "tags": ["Hypercars", "Showroom"]})
        app.get("/api/carmeta", {"id": "vel_corsa_gt3", "favourite": True, "tags": ["GT3"]})
        app.get("/api/carmeta", {"id": "tem_tempest_lmp", "tags": ["Le Mans"]})
        export = app.export(["vel_corsa_gt3"], skin="red_17", lightsOn=True)[0]["file"]
        app.export(["hos_sakura_drift"], preset="game", paint="#e86aa8", textureSize=1024)
        app.export(["kes_nordlicht_rs"], preset="obj")
        app.get("/api/presets/save", {"name": "Night shots", "options": {"preset": "render", "lightsOn": True}})
        # 3. the README pictures
        out_dir = ROOT / "docs" / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, link, size in SHOTS:
            png = out_dir / (name + ".png")
            if "studio=" not in link:
                link += "&studio=studio"
            shoot(browser, profile, "%s/#%s" % (app.base, link.format(export=export)), png, size)
            Image.open(png).convert("RGB").save(png, optimize=True)
            print("shot", png.relative_to(ROOT))
    finally:
        app.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
