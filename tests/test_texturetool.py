"""Converting textures: DDS (compressed and not), TGA, PSD-like inputs, every output format,
resizing, flipping normal maps, and the API."""

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import fixtures as fx
from kn5garage import edits, texturetool
from kn5garage.textures import decode_image
from test_app import AppTestCase


def dxt1_dds(colour=(200, 40, 40), size=64):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, "DDS", pixel_format="DXT1")
    return buf.getvalue()


class TextureToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_output_format_from_a_compressed_dds(self):
        src = self.dir / "body.dds"
        src.write_bytes(dxt1_dds())
        for fmt in texturetool.WRITE:
            with self.subTest(fmt):
                r = texturetool.convert(src, self.dir / "out", fmt)
                out = self.dir / "out" / r["file"]
                img = decode_image(out.read_bytes())
                self.assertEqual(img.size, (64, 64))
                px = img.getpixel((10, 10))[:3]
                self.assertTrue(all(abs(a - b) <= 12 for a, b in zip(px, (200, 40, 40))), (fmt, px))

    def test_resize_flip_and_tga_input(self):
        src = self.dir / "normal.tga"
        Image.new("RGBA", (1024, 512), (128, 100, 255, 255)).save(src)
        r = texturetool.convert(src, self.dir / "out", "png", max_size=256, flip=True)
        self.assertEqual((r["width"], r["height"]), (256, 128))
        img = Image.open(self.dir / "out" / r["file"])
        self.assertEqual(img.getpixel((5, 5))[:3], (128, 155, 255))

    def test_never_overwrites_the_source(self):
        src = self.dir / "logo.png"
        src.write_bytes(fx.png_bytes())
        r = texturetool.convert(src, self.dir, "png")
        self.assertEqual(r["file"], "logo_converted.png")
        self.assertEqual(src.read_bytes(), fx.png_bytes())

    def test_refusals(self):
        bad = self.dir / "x.png"
        bad.write_bytes(b"not a picture")
        exe = self.dir / "x.exe"
        exe.write_bytes(b"MZ")
        for path, fmt, size in ((bad, "png", None), (exe, "png", None), (bad, "exe", None), (bad, "png", 333)):
            with self.subTest(path=path.name, fmt=fmt, size=size), self.assertRaises(edits.EditError):
                texturetool.convert(path, self.dir / "out", fmt, size)


class TextureApiTests(AppTestCase):
    def test_convert_through_the_api(self):
        a, b = self.dir / "a.dds", self.dir / "b.png"
        a.write_bytes(dxt1_dds())
        b.write_bytes(b"broken")
        self.app.pick_textures = lambda: [str(a), str(b)]
        r = self.json("/api/textures/convert", {"format": "png", "maxSize": 512})
        self.assertEqual([f["file"] for f in r["files"]], ["a.png"])
        self.assertEqual([f["from"] for f in r["failed"]], ["b.png"])
        self.assertTrue((self.out / "textures" / "a.png").is_file())
        self.json("/api/textures/convert", {"format": "exe"}, expect=400)
        self.app.pick_textures = lambda: []
        self.assertEqual(self.json("/api/textures/convert", {})["files"], [])


if __name__ == "__main__":
    unittest.main()
