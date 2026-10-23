"""Turning AC textures (DDS, PNG, JPEG) into images glTF accepts (PNG or JPEG)."""

import io
import struct

import numpy as np
from PIL import Image

# Mods ship textures up to 8K. Pillow refuses images over twice this many pixels
# (here 16K x 16K) as damaged or hostile (a "decompression bomb").
Image.MAX_IMAGE_PIXELS = 16384 * 16384 // 2


def decode_dds_uncompressed(data):
    """Fast numpy decoder for uncompressed RGB(A) DDS, which Pillow decodes very slowly.
    Returns None for any other DDS kind (those go to Pillow)."""
    if data[:4] != b"DDS " or len(data) < 128:
        return None
    h, w = struct.unpack_from("<II", data, 12)
    flags, fourcc, bits, rm, gm, bm, am = struct.unpack_from("<I4sIIIII", data, 80)
    if not flags & 0x40 or bits not in (24, 32) or not (rm and gm and bm):
        return None
    bpp = bits // 8
    if w == 0 or h == 0 or len(data) < 128 + w * h * bpp:
        return None
    raw = np.frombuffer(data, np.uint8, w * h * bpp, 128).reshape(h, w, bpp).astype(np.uint32)
    px = raw[..., 0] | raw[..., 1] << 8 | raw[..., 2] << 16
    if bpp == 4:
        px |= raw[..., 3] << 24

    def channel(mask):
        shift = (mask & -mask).bit_length() - 1
        top = mask >> shift
        v = (px & mask) >> shift
        return (v if top == 255 else v * 255 // top).astype(np.uint8)

    bands = [channel(rm), channel(gm), channel(bm)]
    if flags & 0x1 and am:
        bands.append(channel(am))
    return Image.fromarray(np.ascontiguousarray(np.dstack(bands)))


def decode_image(data):
    img = decode_dds_uncompressed(data)
    if img is None:
        img = Image.open(io.BytesIO(data))
        img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return img


def png_bytes(img):
    out = io.BytesIO()
    img.save(out, "PNG", compress_level=4)
    return out.getvalue()


def image_for_gltf(data, flip_green=False, max_size=None, jpeg_quality=None):
    """Return (bytes, mime) in a format glTF accepts (PNG or JPEG), optionally scaled down.
    jpeg_quality: store images without transparency as JPEG (much smaller, slightly lossy)."""
    is_png, is_jpeg = data[:8] == b"\x89PNG\r\n\x1a\n", data[:3] == b"\xff\xd8\xff"
    if not flip_green and not max_size and (is_jpeg or (is_png and not jpeg_quality)):
        return data, "image/jpeg" if is_jpeg else "image/png"
    img = decode_image(data)
    if max_size and max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    if flip_green:
        bands = list(img.split())
        bands[1] = bands[1].point(lambda v: 255 - v)
        img = Image.merge(img.mode, bands)
    if img.mode == "RGBA" and img.getextrema()[3] == (255, 255):
        img = img.convert("RGB")
    if jpeg_quality and img.mode == "RGB":
        out = io.BytesIO()
        img.save(out, "JPEG", quality=int(jpeg_quality), optimize=True)
        return out.getvalue(), "image/jpeg"
    return png_bytes(img), "image/png"


def dds_bytes(img, mipmaps=True):
    """An uncompressed 32-bit BGRA .dds with a full mipmap chain: what AC reads for skins.
    (Uncompressed keeps every detail of a livery; the game compresses nothing itself.)"""
    img = img.convert("RGBA")
    levels = [img]
    while mipmaps and max(levels[-1].size) > 1:
        w, h = levels[-1].size
        levels.append(levels[-1].resize((max(1, w // 2), max(1, h // 2)), Image.LANCZOS))
    w, h = img.size
    flags = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000 | (0x20000 if len(levels) > 1 else 0)
    caps = 0x1000 | (0x8 | 0x400000 if len(levels) > 1 else 0)
    header = struct.pack("<4sIIIIIII44x", b"DDS ", 124, flags, h, w, w * 4, 0, len(levels))
    header += struct.pack("<II4sIIIII", 32, 0x41, b"\0\0\0\0", 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    header += struct.pack("<IIII4x", caps, 0, 0, 0)
    body = b"".join(np.asarray(level, np.uint8)[..., [2, 1, 0, 3]].tobytes() for level in levels)
    return header + body


def image_size(data):
    """(width, height) of an encoded PNG / JPEG without decoding the pixels."""
    with Image.open(io.BytesIO(data)) as probe:
        return probe.size


def metal_rough_png(data, spec_exp, metal_scale, max_size=None):
    """AC's txMaps (R specular, G gloss, B reflection) -> a glTF metallic-roughness
    texture (G roughness, B metallic) as PNG bytes."""
    img = decode_image(data).convert("RGB")
    if max_size and max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    a = np.asarray(img, np.float32) / 255
    gloss, refl = a[..., 1], a[..., 2]
    rough = np.sqrt(2.0 / (np.maximum(spec_exp, 1.0) * np.maximum(gloss, 0.02) + 2.0))
    out = np.dstack([np.ones_like(gloss), np.clip(rough, 0.03, 1), np.clip(refl * metal_scale, 0, 1)])
    return png_bytes(Image.fromarray((out * 255 + 0.5).astype(np.uint8)))
