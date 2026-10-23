"""Builders for synthetic kn5 files and Assetto Corsa car folders used by the tests."""

import io
import json
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- images

def png_bytes(color=(200, 30, 30), size=8):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(color=(30, 200, 30), size=8):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG")
    return buf.getvalue()


def dds_uncompressed(pixels, bits=32):
    """pixels: HxWx4 uint8 RGBA array. Writes a classic uncompressed BGR(A) DDS."""
    h, w = pixels.shape[:2]
    bpp = bits // 8
    pf_flags = 0x41 if bits == 32 else 0x40
    header = struct.pack("<4sIIIIIII44x", b"DDS ", 124, 0x100F, h, w, w * bpp, 0, 0)
    header += struct.pack("<II4sIIIII", 32, pf_flags, b"\0\0\0\0", bits,
                          0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000 if bits == 32 else 0)
    header += struct.pack("<IIII4x", 0x1000, 0, 0, 0)
    assert len(header) == 128
    bgra = pixels[..., [2, 1, 0, 3]] if bits == 32 else pixels[..., [2, 1, 0]]
    return header + np.ascontiguousarray(bgra, dtype=np.uint8).tobytes()


def dds_solid(color=(200, 200, 200, 255), size=8):
    return dds_uncompressed(np.full((size, size, 4), color, np.uint8))


def gradient_pixels(w=16, h=8):
    y, x = np.mgrid[0:h, 0:w]
    return np.dstack([x * 15 % 256, y * 30 % 256, (x + y) * 7 % 256, 255 - x * 3]).astype(np.uint8)


# ---------------------------------------------------------------- kn5 pieces

def s(text):
    b = text.encode("utf-8")
    return struct.pack("<i", len(b)) + b


def box_geometry(sx=1.0, sy=1.0, sz=1.0, winding="cw", centre=(0, 0, 0)):
    """24 vertices (pos, normal, uv, tangent) and 36 indices. 'cw' = DirectX style."""
    verts, idx = [], []
    for axis in range(3):
        for sign in (1, -1):
            n = np.zeros(3); n[axis] = sign
            u = np.zeros(3); u[(axis + 1) % 3] = 1
            v = np.cross(n, u)
            base = len(verts)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = (n + u * a + v * b) * (sx, sy, sz) + centre
                verts.append(list(p) + list(n) + [(a + 1) / 2, (b + 1) / 2] + list(u))
            if winding == "cw":
                idx += [base, base + 2, base + 1, base, base + 3, base + 2]
            else:
                idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return np.array(verts, np.float32), np.array(idx, np.uint16)


def material(name, shader="ksPerPixel", maps=(), blend=0, alpha_tested=False, props=(), version=6):
    b = s(name) + s(shader) + bytes([blend, int(alpha_tested)])
    if version > 4:
        b += struct.pack("<i", 0)
    b += struct.pack("<i", len(props))
    for pname, a, c in props:
        b += s(pname) + struct.pack("<f", a) + struct.pack("<2f", 0, 0)
        b += struct.pack("<3f", *c) + struct.pack("<4f", 0, 0, 0, 0)
    b += struct.pack("<i", len(maps))
    for slot, (sampler, tex) in enumerate(maps):
        b += s(sampler) + struct.pack("<i", slot) + s(tex)
    return b


def dummy(name, children=(), translation=(0, 0, 0), active=True, matrix=None):
    m = np.eye(4, dtype=np.float32) if matrix is None else np.asarray(matrix, np.float32)
    m = m.copy()
    m[3, :3] += translation  # DirectX: translation in the 4th row
    b = struct.pack("<i", 1) + s(name) + struct.pack("<i", len(children)) + bytes([int(active)])
    return b + m.tobytes() + b"".join(children)


def mesh(name, mat=0, verts=None, indices=None, skinned=False, visible=True, children=()):
    if verts is None:
        verts, indices = box_geometry()
    b = struct.pack("<i", 3 if skinned else 2) + s(name) + struct.pack("<i", len(children)) + b"\x01"
    b += bytes([1, int(visible), 0])
    if skinned:
        b += struct.pack("<i", 1) + s("bone0") + np.eye(4, dtype=np.float32).tobytes()
        extra = np.zeros((len(verts), 8), np.float32)
        extra[:, 0] = 1
        b += struct.pack("<i", len(verts)) + np.hstack([verts, extra]).astype(np.float32).tobytes()
    else:
        b += struct.pack("<i", len(verts)) + np.asarray(verts, np.float32).tobytes()
    b += struct.pack("<i", len(indices)) + np.asarray(indices, np.uint16).tobytes()
    b += struct.pack("<ii", mat, 0)
    if skinned:
        b += b"\0" * 8
    else:
        b += struct.pack("<2f", 0, 0) + struct.pack("<4f", 0, 0, 0, 3) + b"\x01"
    return b + b"".join(children)


def kn5_bytes(root, textures=None, materials=(), version=6, trailer=b""):
    textures = textures or {}
    b = b"sc6969" + struct.pack("<i", version)
    if version > 5:
        b += struct.pack("<i", 0)
    b += struct.pack("<i", len(textures))
    for name, data in textures.items():
        b += struct.pack("<i", 1) + s(name) + struct.pack("<i", len(data)) + data
    b += struct.pack("<i", len(materials)) + b"".join(materials)
    return b + root + trailer


def car_kn5(version=6, protected=False):
    """A small car: body (paint + normal map), glass, 4 wheels, steering wheel, skinned driver."""
    if protected:
        textures = {n: png_bytes((0, 0, 255), 1) for n in ("body.dds", "body_nm.dds", "wheel.dds")}
        trailer = s("acd.checksum") + b"\x99" * 400
    else:
        textures = {"body.dds": dds_solid((200, 200, 200, 255)),
                    "body_nm.dds": dds_solid((128, 100, 255, 255)),
                    "wheel.dds": png_bytes((30, 30, 30))}
        trailer = b""
    mats = [
        material("EXT_Carpaint", "ksPerPixelMultiMap", [("txDiffuse", "body.dds"), ("txNormal", "body_nm.dds")],
                 props=[("ksSpecularEXP", 80, (0, 0, 0))], version=version),
        material("Tyre", "ksPerPixel", [("txDiffuse", "wheel.dds")],
                 props=[("ksSpecularEXP", 5, (0, 0, 0))], version=version),
        material("Glass", "ksWindscreen", [], blend=1, version=version),
    ]
    wheels = [dummy("WHEEL_%s" % n, [mesh("tyre_%s" % n, 1, *box_geometry(0.12, 0.33, 0.33))], t)
              for n, t in (("LF", (0.8, 0.33, 1.3)), ("RF", (-0.8, 0.33, 1.3)),
                           ("LR", (0.8, 0.33, -1.3)), ("RR", (-0.8, 0.33, -1.3)))]
    steer = dummy("STEER_HR", [mesh("steer_mesh", 1, *box_geometry(0.02, 0.18, 0.18))], (0.4, 0.9, 0.3))
    body = dummy("BODY", [mesh("body", 0, *box_geometry(0.9, 0.35, 2.2)),
                          mesh("glass", 2, *box_geometry(0.7, 0.2, 0.8))], (0, 0.6, 0))
    driver = mesh("driver_skinned", 1, *box_geometry(0.2, 0.3, 0.2), skinned=True)
    root = dummy("car.kn5", [body, steer, driver] + wheels)
    return kn5_bytes(root, textures, mats, version=version, trailer=trailer)


def skinned_mesh(name, bones, weights, joints, mat=0, verts=None, indices=None):
    """A skinned mesh with real bones: bones = [(name, 4x4 row-vector inverse bind matrix)]."""
    if verts is None:
        verts, indices = box_geometry(0.2, 0.3, 0.2)
    b = struct.pack("<i", 3) + s(name) + struct.pack("<i", 0) + b"\x01" + bytes([1, 1, 0])
    b += struct.pack("<i", len(bones))
    for bname, m in bones:
        b += s(bname) + np.asarray(m, np.float32).reshape(16).tobytes()
    extra = np.hstack([np.broadcast_to(np.asarray(weights, np.float32), (len(verts), 4)),
                       np.broadcast_to(np.asarray(joints, np.float32), (len(verts), 4))])
    b += struct.pack("<i", len(verts)) + np.hstack([verts, extra]).astype(np.float32).tobytes()
    b += struct.pack("<i", len(indices)) + np.asarray(indices, np.uint16).tobytes()
    b += struct.pack("<ii", mat, 0) + b"\0" * 8
    return b


def rigged_kn5(bone_offset=(0, 1, 0), break_bind=False, missing_bone=False):
    """A driver: two bone dummies and a skinned mesh weighted between them."""
    eye = np.eye(4, dtype=np.float32)
    inv = eye.copy()
    inv[3, :3] = [-v for v in bone_offset]  # inverse of the bone's translation (row-vector form)
    if break_bind:
        inv[3, 0] += 5
    bones = [("DRIVER_ROOT", eye), ("DRIVER_HEAD", inv)]
    if missing_bone:
        bones.append(("NOT_A_NODE", eye))
    skinned = skinned_mesh("driver_body", bones, weights=(0.6, 0.6, 0, 0), joints=(0, 1, 0, 0))
    root = dummy("driver.kn5", [dummy("DRIVER_ROOT", [dummy("DRIVER_HEAD", translation=bone_offset)]), skinned])
    return kn5_bytes(root, {}, [material("Driver_Suit")])


def lod_kn5():
    """A low-detail stand-in: one box, one material."""
    root = dummy("lod.kn5", [mesh("lod_body", 0, *box_geometry(0.9, 0.4, 2.2))])
    return kn5_bytes(root, {"body.dds": dds_solid((200, 200, 200, 255))},
                     [material("EXT_Carpaint", maps=[("txDiffuse", "body.dds")])])


def ksanim_bytes(tracks, version=2):
    """tracks: {node name: list of frames}. v2 frame = (rot xyzw, pos xyz, scale xyz);
    v1 frame = a 4x4 row-vector matrix (16 floats)."""
    b = struct.pack("<ii", version, len(tracks))
    for name, frames in tracks.items():
        b += s(name) + struct.pack("<i", len(frames))
        for f in frames:
            b += np.asarray(f, np.float32).tobytes()
    return b


def door_anim(frames=3):
    """STEER_HR turning 0..90 degrees around Y while moving up 0.1 m."""
    out = []
    for i in range(frames):
        a = np.radians(90 * i / max(frames - 1, 1))
        out.append([0, np.sin(a / 2), 0, np.cos(a / 2), 0.4, 0.9 + 0.1 * i / max(frames - 1, 1), 0.3, 1, 1, 1])
    return out


UI_CAR = {
    "name": "Test Racer GT",
    "brand": "Testa",
    "description": "Fast<br>car &amp; friends",
    "tags": ["gt", "test"],
    "class": "race",
    "specs": {"bhp": "500 bhp", "torque": "600 Nm", "weight": "1200 kg",
              "topspeed": "300 km/h", "acceleration": "3.1 s", "pwratio": "2.4 kg/hp"},
    "torqueCurve": [[0, 100], [4000, 600], [8000, 450]],
    "powerCurve": [[0, 0], [4000, 300], [8000, 500]],
    "year": 2024, "country": "Italy", "author": "Jane Modder",
    "url": "https://example.com/mods", "version": "1.2",
}


def make_car(parent, name="testcar", *, protected=False, encrypted=False, model=True,
             skins=(("00_red", {"body.dds": dds_solid((220, 20, 20, 255))}),
                    ("01_blue", {"BODY.png": png_bytes((20, 40, 200))})),
             ui=UI_CAR, lods_ini=False, version=6, lod=False, animations=None):
    """lod=True writes a real LOD_B model; animations = {file stem: ksanim bytes}."""
    car = Path(parent) / name
    (car / "ui").mkdir(parents=True, exist_ok=True)
    if model:
        if encrypted:
            (car / ("%s.kn5" % name)).write_bytes(b"\x13\x37" * 5000)
        else:
            data = car_kn5(version=version, protected=protected)
            (car / ("%s.kn5" % name)).write_bytes(data)
            (car / ("%s_lod_b.kn5" % name)).write_bytes(lod_kn5() if lod else data[:200])
            (car / "collider.kn5").write_bytes(data[:100])
    for stem, raw in (animations or {}).items():
        (car / "animations").mkdir(exist_ok=True)
        (car / "animations" / (stem + ".ksanim")).write_bytes(raw)
    if lods_ini:
        (car / "data").mkdir(exist_ok=True)
        (car / "data" / "lods.ini").write_text("[LOD_0]\nFILE=%s.kn5\nOUT=15\n" % name)
    if ui is not None:
        text = ui if isinstance(ui, str) else json.dumps(ui, indent=2)
        (car / "ui" / "ui_car.json").write_text(text, encoding="utf-8")
        (car / "ui" / "badge.png").write_bytes(png_bytes((255, 255, 255), 4))
    for skin_name, files in skins:
        d = car / "skins" / skin_name
        d.mkdir(parents=True, exist_ok=True)
        for fname, data in files.items():
            (d / fname).write_bytes(data)
        (d / "preview.jpg").write_bytes(jpeg_bytes())
        (d / "ui_skin.json").write_text(json.dumps({"skinname": skin_name.upper(), "drivername": "Driver " + skin_name,
                                                   "number": "7", "team": "Team T"}))
    return car


def make_garage(parent):
    """A cars folder with one car of each kind."""
    parent = Path(parent)
    make_car(parent, "a_normal")
    make_car(parent, "b_protected", protected=True,
             ui=dict(UI_CAR, name="Locked Car", author="Secret Crew", url=None))
    make_car(parent, "c_encrypted", encrypted=True, ui=None, skins=())
    make_car(parent, "d_broken_json", ui='﻿{"name": "Broken, json", "author": "X",}', skins=())
    (parent / "e_not_a_car" / "ui").mkdir(parents=True, exist_ok=True)
    return parent
