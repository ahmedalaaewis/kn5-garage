"""The library beyond Assetto Corsa: BeamNG.drive vehicles (folders and zip mods) and loose 3D
files are found, summarised, previewed and exported; AC-only tools refuse them politely."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import fixtures as fx
from kn5garage import beamng, importer, items
from test_app import AppTestCase

DAE = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
 <asset><unit meter="1"/><up_axis>Z_UP</up_axis></asset>
 <library_materials><material id="body_m" name="truck_body"/></library_materials>
 <library_geometries>
  {geoms}
 </library_geometries>
 <library_visual_scenes><visual_scene id="s">{nodes}</visual_scene></library_visual_scenes>
 <scene><instance_visual_scene url="#s"/></scene>
</COLLADA>"""
GEOM = """<geometry id="{n}" name="{n}"><mesh>
  <source id="{n}p"><float_array id="{n}a" count="9">0 0 0 {s} 0 0 0 0 {s}</float_array>
   <technique_common><accessor source="#{n}a" count="3" stride="3"/></technique_common></source>
  <vertices id="{n}v"><input semantic="POSITION" source="#{n}p"/></vertices>
  <triangles material="m" count="1"><input semantic="VERTEX" source="#{n}v" offset="0"/><p>0 1 2</p></triangles>
 </mesh></geometry>"""
NODE = """<node name="{n}"><instance_geometry url="#{n}"><bind_material><technique_common>
  <instance_material symbol="m" target="#body_m"/></technique_common></bind_material></instance_geometry></node>"""

INFO = """{
  // BeamNG's relaxed JSON: comments, a missing comma, trailing commas
  "Name": "Desert Truck"
  "Brand": "Gavril",
  "Years": {"min": 1995, "max": 2004,},
  "Body Style": "Pickup",
  "Author": "Demo",
  "default_pc": "base",
}"""
JBEAM = """{
 "truck_main": {
   "slotType": "main",
   "slots": [["type", "default", "description"], ["truck_bumper", "truck_bumper_steel", "Bumper"]],
   "flexbodies": [["mesh", "[group]:"], ["truck_body", ["body"]]],
 },
 "truck_bumper_steel": {"slotType": "truck_bumper", "flexbodies": [["mesh", "[group]:"], ["bumper_steel", []]]},
 "truck_bumper_chrome": {"slotType": "truck_bumper", "flexbodies": [["mesh", "[group]:"], ["bumper_chrome", []]]},
}"""


def make_vehicle(folder, name="truck", config_bumper="truck_bumper_steel"):
    d = Path(folder) / name
    d.mkdir(parents=True)
    geoms = "".join(GEOM.format(n=n, s=s) for n, s in (("truck_body", 4), ("bumper_steel", 1), ("bumper_chrome", 1)))
    nodes = "".join(NODE.format(n=n) for n in ("truck_body", "bumper_steel", "bumper_chrome"))
    (d / "truck.dae").write_text(DAE.format(geoms=geoms, nodes=nodes))
    (d / "info.json").write_text(INFO)
    (d / "info_base.json").write_text('{"Power": 210.4, "Torque": 400, "Weight": 1900, "Top Speed": 50}')
    (d / "truck.jbeam").write_text(JBEAM)
    (d / "base.pc").write_text(json.dumps({"parts": {"truck_bumper": config_bumper}}))
    (d / "body_d.png").write_bytes(fx.png_bytes((200, 30, 30)))
    (d / "police_d.png").write_bytes(fx.png_bytes((10, 10, 200)))
    (d / "main.materials.json").write_text(json.dumps({
        "truck_body": {"mapTo": "truck_body", "Stages": [{"colorMap": "/vehicles/truck/body_d.dds"}]},
        "truck_body_police": {"mapTo": "truck_body.skin.police", "Stages": [{"colorMap": "/vehicles/truck/police_d.png"}]},
    }))
    return d


def mesh_names(model):
    return sorted(n["name"] for n in importer.walk(model["root"]) if n["cls"] != 1)


class BeamNGTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_relaxed_json(self):
        data = beamng.relaxed_json('{"a": 1 "b": [1 2 3,], // note\n "t": "x 5 6" /* c */ "o": {"k": true,},}')
        self.assertEqual(data, {"a": 1, "b": [1, 2, 3], "t": "x 5 6", "o": {"k": True}})

    def test_default_configuration_picks_the_parts(self):
        d = make_vehicle(self.dir)
        model = beamng.load_vehicle(d)
        self.assertEqual(mesh_names(model), ["bumper_steel", "truck_body"])  # not the chrome bumper
        chrome = make_vehicle(self.dir / "b", config_bumper="truck_bumper_chrome")
        self.assertEqual(mesh_names(beamng.load_vehicle(chrome)), ["bumper_chrome", "truck_body"])
        lo, hi = importer.bounds(model["root"])
        self.assertAlmostEqual(float(hi[1]), 4.0)  # Z up became Y up

    def test_materials_textures_and_skins(self):
        d = make_vehicle(self.dir)
        self.assertEqual(beamng.skins(d), ["police"])
        from kn5garage.textures import decode_image
        for skin, colour in ((None, (200, 30, 30)), ("police", (10, 10, 200))):
            model = beamng.load_vehicle(d, skin=skin)
            mat = next(m for m in model["materials"] if m["name"] == "truck_body")
            self.assertEqual(decode_image(model["textures"][mat["maps"]["txDiffuse"]]).getpixel((0, 0))[:3], colour)

    def test_summary_and_zip_mod(self):
        d = make_vehicle(self.dir / "vehicles")
        s = items.summary(items.Item("beamng", d))
        self.assertEqual((s["name"], s["brand"], s["year"], s["carClass"]), ("Desert Truck", "Gavril", "1995", "Pickup"))
        self.assertEqual(s["specs"], {"bhp": "210 hp", "torque": "400 Nm", "weight": "1900 kg", "topspeed": "180 km/h"})
        z = self.dir / "mods" / "truck_mod.zip"
        z.parent.mkdir()
        with zipfile.ZipFile(z, "w") as zf:
            for f in d.rglob("*"):
                zf.write(f, "vehicles/truck/" + f.relative_to(d).as_posix())
            zf.writestr("vehicles/truck/evil.exe", b"MZ")
        found = items.find_items(self.dir / "mods")
        self.assertEqual([(i.kind, i.inner) for i in found], [("beamng", "vehicles/truck")])
        self.assertEqual(items.summary(found[0])["name"], "Desert Truck")
        model = items.load_model(found[0], cache_root=self.dir / "cache")
        self.assertEqual(mesh_names(model), ["bumper_steel", "truck_body"])
        unpacked = next((self.dir / "cache" / "beamng").iterdir())
        self.assertFalse((unpacked / "evil.exe").exists())

    def test_find_items_mixes_kinds_and_skips_output(self):
        root = self.dir / "stuff"
        fx.make_car(root / "ac", "gt")
        make_vehicle(root / "beam")
        (root / "figures").mkdir()
        (root / "figures" / "knight.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        (root / "figures" / "notes.txt").write_text("x")
        out = root / "exports"
        out.mkdir()
        (out / "gt.glb").write_bytes(b"glTF")
        kinds = sorted((i.kind, i.path.name) for i in items.find_items(root, exclude=[out]))
        self.assertEqual(kinds, [("ac", "gt"), ("beamng", "truck"), ("model", "knight.obj")])


class ItemsApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        make_vehicle(self.cars / "beamng" / "vehicles")
        (self.cars / "figures").mkdir()
        (self.cars / "figures" / "statue.stl").write_text(
            "solid s\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 10 0 0\nvertex 0 10 0\nendloop\nendfacet\nendsolid s\n")

    def test_scan_preview_export_and_refusals(self):
        cars = {c["id"]: c for c in self.scan()["cars"]}
        self.assertEqual(cars["truck"]["kind"], "beamng")
        self.assertEqual(cars["statue"]["kind"], "model")
        self.assertEqual(cars["a_normal"]["kind"], "ac")
        d = self.json("/api/car?id=truck")
        self.assertEqual([s["id"] for s in d["skins"]], ["default", "police"])
        for car, skin in (("truck", "police"), ("statue", "")):
            with self.subTest(car):
                url = self.json("/api/preview?id=%s&skin=%s" % (car, skin))["url"]
                self.assertEqual(self.request(url + "?t=" + self.app.token, token=False)[0], 200)
        job = self.json("/api/convert", {"ids": ["truck", "statue"], "preset": "obj"})["job"]
        results = self.wait_job(job)["results"]
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertTrue((self.out / "statue__obj.obj").is_file())
        self.json("/api/carmeta", {"id": "statue", "favourite": True})
        self.assertTrue({c["id"]: c for c in self.scan()["cars"]}["statue"]["favourite"])
        for path, body in (("/api/edit/specs", {"id": "truck", "target": {"mode": "original"}}),
                           ("/api/livery/start", {"id": "statue"}), ("/api/parts/start", {"id": "truck"})):
            with self.subTest(path):
                r = self.json(path, body, expect=400)
                self.assertIn("Assetto Corsa", r["error"])


if __name__ == "__main__":
    unittest.main()
