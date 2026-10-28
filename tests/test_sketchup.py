"""SketchUp .skp / .skb (2021 and newer): a small file is written in the same layout as
SketchUp's (header, ZIP, material.xml, model.dat records) and read back."""

import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

import fixtures  # noqa: F401 (puts the project on the import path)
from kn5garage import formats, importer
from kn5garage.formats import sketchup
from kn5garage.importer import ImportError3D


def rec(tag, *body):
    data = b"".join(body)
    return struct.pack("<HI", tag, len(data)) + data


def ident(i, size=2):
    return rec(1500, rec(1502, i.to_bytes(size, "little")))


def element(i, material=None):
    return rec(2000, ident(i), rec(2001, material.to_bytes(2, "little")) if material else b"", rec(2003, b"\x06"))


def entities(verts, edges, faces, instances=(), own_id=None):
    out = []
    if own_id is not None:
        out.append(rec(2000, ident(own_id)))
    out.append(rec(5001, *[rec(2500, ident(i), rec(2501, struct.pack("<3d", *p))) for i, p in verts]))
    out.append(rec(5002, *[rec(3000, element(i), rec(3001, a.to_bytes(2, "little")), rec(3002, b.to_bytes(2, "little")))
                           for i, (a, b) in edges]))
    fs = []
    for i, mat, plane, loops in faces:
        loop_recs = [rec(4500, rec(4501, *[rec(4000, rec(4001, e.to_bytes(2, "little")), rec(4002, b"\x01")) for e in lp]))
                     for lp in loops]
        fs.append(rec(3500, element(i, mat), rec(3501, struct.pack("<4d", *plane)), rec(3502, *loop_recs)))
    out.append(rec(5003, *fs))
    groups = [rec(6500, element(900 + k, mat), rec(6501), rec(6502, struct.pack("<13d", *t)),
                  rec(6503, ref.to_bytes(2, "little")), rec(6504, b"\0" * 16)) for k, (ref, t, mat) in enumerate(instances)]
    out.append(rec(5004, *groups))
    return rec(5000, *out)


def square_with_hole():
    """A 10 x 10 inch square at z = 0 with a 4 x 4 hole in the middle."""
    v = [(1, (0, 0, 0)), (2, (10, 0, 0)), (3, (10, 10, 0)), (4, (0, 10, 0)),
         (5, (3, 3, 0)), (6, (7, 3, 0)), (7, (7, 7, 0)), (8, (3, 7, 0))]
    e = [(11, (1, 2)), (12, (2, 3)), (13, (3, 4)), (14, (4, 1)), (15, (5, 6)), (16, (6, 7)), (17, (7, 8)), (18, (8, 5))]
    f = [(21, None, (0, 0, 1, 0), [[11, 12, 13, 14], [15, 16, 17, 18]])]
    return v, e, f


def skp_bytes(version="26.1.194", red=(200, 30, 30), move=(1, 0, 0, 0, 1, 0, 0, 0, 1, 100, 0, 0, 1)):
    """move: the group's transform as SketchUp stores it (default: 100 inches along X)."""
    v, e, f = square_with_hole()
    definition = rec(5500, entities(v, e, f, own_id=500), rec(5502, b"Plate"))
    root = entities([], [], [], instances=[(500, move, 0x8001)])
    materials = rec(12500, rec(12501, rec(13000, ident(0x8001), rec(13004, b"Red"))))
    model = rec(0x01F4, rec(0x0063, b"\0\0\0\0"), rec(0x01F6, root), rec(0x01F7, materials),
                rec(0x01F9, rec(6000, rec(6001, definition))))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("model.dat", model)
        z.writestr("materials/Red/material.xml",
                   '<?xml version="1.0"?><materialDocument xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">'
                   '<mat:material name="Red" colorRed="%d" colorGreen="%d" colorBlue="%d" trans="0" useTrans="0"/>'
                   '</materialDocument>' % red)
    head = b"\xff\xfe\xff\x0e" + "SketchUp Model".encode("utf-16-le") + b"\xff\xfe\xff" + \
        bytes([len(version) + 2]) + ("{%s}" % version).encode("utf-16-le") + b"VFF\x08\x00\x01\x00\x11\x00"
    return head + buf.getvalue()


class SketchUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_faces_holes_groups_materials_units(self):
        p = self.dir / "plate.skp"
        p.write_bytes(skp_bytes())
        m = formats.load(p)
        meshes = [n for n in importer.walk(m["root"]) if n["cls"] != 1]
        tris = sum(len(n["indices"]) // 3 for n in meshes)
        self.assertEqual(tris, 16)  # a square ring is 8 triangles, both sides
        lo, hi = importer.bounds(m["root"])
        np.testing.assert_allclose(lo, [100 * 0.0254, 0, -10 * 0.0254], atol=1e-9)  # moved 100", Z up -> Y up
        np.testing.assert_allclose(hi, [110 * 0.0254, 0, 0], atol=1e-9)
        # the hole stays open: no triangle centre lies inside it
        area = 0.0
        for n in meshes:
            v = n["verts"][:, :3].astype(np.float64)
            t = v[np.asarray(n["indices"]).reshape(-1, 3)]
            area += np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2
        self.assertAlmostEqual(area, 2 * (100 - 16), places=6)  # inches^2 in the definition, both sides
        mat = m["materials"][meshes[0]["material"]]
        self.assertEqual(mat["name"], "Red")  # inherited from the group
        from kn5garage.textures import decode_image
        self.assertEqual(decode_image(m["textures"][mat["maps"]["txDiffuse"]]).getpixel((0, 0))[:3], (200, 30, 30))
        self.assertEqual(m["sketchup_version"], "26.1.194")

    def test_rotation_is_stored_row_by_row(self):
        # a quarter turn about Z, as SketchUp writes it: rows (0 -1 0), (1 0 0), (0 0 1);
        # read the other way round, parts of real models fly apart
        p = self.dir / "turned.skp"
        p.write_bytes(skp_bytes(move=(0, -1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1)))
        lo, hi = importer.bounds(formats.load(p)["root"])
        # the 10 x 10 inch square at x, y >= 0 turns to x <= 0; SketchUp's Y becomes -Z here
        np.testing.assert_allclose(lo, [-10 * 0.0254, 0, -10 * 0.0254], atol=1e-9)
        np.testing.assert_allclose(hi, [0, 0, 0], atol=1e-9)

    def test_backup_files_and_old_versions(self):
        skb = self.dir / "plate.skb"
        skb.write_bytes(skp_bytes())
        self.assertTrue(formats.load(skb)["root"]["children"])
        old = self.dir / "old.skp"
        old.write_bytes(b"\xff\xfe\xff\x0e" + "SketchUp Model".encode("utf-16-le") + b"\xff\xfe\xff\x0a"
                        + "{20.0.363}".encode("utf-16-le") + b"\x00" * 200)
        with self.assertRaises(ImportError3D) as e:
            formats.load(old)
        self.assertIn("2021", str(e.exception))
        for bad in (b"not sketchup", skp_bytes()[:150], skp_bytes()[:-60]):
            p = self.dir / "bad.skp"
            p.write_bytes(bad)
            with self.subTest(size=len(bad)), self.assertRaises(ImportError3D):
                formats.load(p)

    def test_concave_outline(self):
        # an L-shaped face: ear clipping must not fill the notch
        loop2d = [((0, 0), 1), ((2, 0), 2), ((2, 1), 3), ((1, 1), 4), ((1, 2), 5), ((0, 2), 6)]
        pts = dict((i, p) for p, i in loop2d)
        tris = sketchup.triangulate([loop2d])
        area = sum(abs((pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (pts[b][1] - pts[a][1]) * (pts[c][0] - pts[a][0])) / 2
                   for a, b, c in tris)
        self.assertEqual(len(tris), 4)
        self.assertAlmostEqual(area, 3.0)


if __name__ == "__main__":
    unittest.main()
