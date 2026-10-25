"""Textures for any game or 3D program: open DDS (compressed or not), TGA, PSD (the flattened
picture), PNG, JPEG and more, and save them as PNG, JPEG, TGA, BMP, WebP or DDS, optionally
smaller, and with the green channel flipped (normal maps: DirectX <-> OpenGL)."""

import io
from pathlib import Path

from PIL import Image

from .edits import EditError
from .textures import dds_bytes, decode_image

READ = (".png", ".jpg", ".jpeg", ".dds", ".tga", ".psd", ".bmp", ".tif", ".tiff", ".webp", ".gif")
WRITE = ("png", "jpg", "tga", "bmp", "webp", "dds")
SIZES = (256, 512, 1024, 2048, 4096, 8192)
MAX_FILES = 200
MAX_SIDE = 16384


def open_image(path):
    path = Path(path)
    if path.suffix.lower() not in READ:
        raise EditError("%s isn't a picture this tool reads" % path.name)
    data = path.read_bytes()
    try:
        img = decode_image(data)  # our DDS reader first (uncompressed DDS), then Pillow
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError) as e:
        raise EditError("%s couldn't be read: %s" % (path.name, e))
    if max(img.size) > MAX_SIDE:
        raise EditError("%s is larger than %d pixels" % (path.name, MAX_SIDE))
    return img


def info(path):
    path = Path(path)
    img = open_image(path)
    return {"name": path.name, "width": img.size[0], "height": img.size[1], "alpha": img.mode == "RGBA",
            "bytes": path.stat().st_size}


def flip_green(img):
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r, g.point(lambda v: 255 - v), b, a))


def encode(img, fmt, quality=92):
    buf = io.BytesIO()
    if fmt == "dds":
        return dds_bytes(img.convert("RGBA"))
    if fmt == "jpg":
        img.convert("RGB").save(buf, "JPEG", quality=quality)
    elif fmt == "bmp":
        img.convert("RGB").save(buf, "BMP")
    elif fmt == "tga":
        img.convert("RGBA").save(buf, "TGA", rle=True)
    elif fmt == "webp":
        img.convert("RGBA").save(buf, "WEBP", quality=quality)
    else:
        img.convert("RGBA").save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def convert(path, out_dir, fmt, max_size=None, flip=False, quality=92):
    """One picture -> out_dir/<name>.<fmt>; returns the new file's path and its size."""
    if fmt not in WRITE:
        raise EditError("format must be one of %s" % ", ".join(WRITE))
    if max_size is not None and max_size not in SIZES:
        raise EditError("size must be one of %s" % ", ".join(map(str, SIZES)))
    path = Path(path)
    img = open_image(path)
    if max_size and max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    if flip:
        img = flip_green(img)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("%s.%s" % (path.stem, fmt))
    if out.resolve() == path.resolve():  # never overwrite the picture we read
        out = out_dir / ("%s_converted.%s" % (path.stem, fmt))
    out.write_bytes(encode(img, fmt, quality))
    return {"from": path.name, "file": out.name, "width": img.size[0], "height": img.size[1], "bytes": out.stat().st_size}
