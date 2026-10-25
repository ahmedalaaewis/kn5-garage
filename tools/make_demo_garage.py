"""
Build a demo garage of fictional cars (procedural models, made-up brands) for trying the
app, for the screenshots in the README and for manual testing. Nothing here comes from real mods.

    python tools/make_demo_garage.py [folder]      (default: .dev/demo_cars)
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import fixtures as fx  # noqa: E402  (the kn5 writer used by the tests)

# ---------------------------------------------------------------- geometry


def mesh_data(pos, tri, uv=None):
    """(verts n x 11, indices) with smooth normals computed from the triangles."""
    pos = np.asarray(pos, np.float64)
    tri = np.asarray(tri, np.int64)
    nrm = np.zeros_like(pos)
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    for k in range(3):
        np.add.at(nrm, tri[:, k], face)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    uv = np.zeros((len(pos), 2)) if uv is None else np.asarray(uv, np.float64)
    tan = np.tile([1.0, 0, 0], (len(pos), 1))
    verts = np.hstack([pos, nrm, uv, tan]).astype(np.float32)
    # kn5 / DirectX winding is clockwise; the converter fixes either way, but stay faithful
    return verts, tri[:, [0, 2, 1]].reshape(-1).astype(np.uint16)


def loft(stations, ring):
    """Join rings of points (one per station) into a closed-around surface; uv = (station, ring)."""
    s, r = len(stations), ring
    pos = np.concatenate(stations)
    tri = []
    for i in range(s - 1):
        for j in range(r):
            a, b = i * r + j, i * r + (j + 1) % r
            c, d = a + r, b + r
            tri += [(a, b, c), (b, d, c)]  # counter-clockwise from outside: normals point out
    uv = [(i / (s - 1), j / r) for i in range(s) for j in range(r)]
    return pos, tri, uv


def superellipse(cx, cy, w, h, n=24, p=3.2, bottom_flat=True):
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        c, s = math.cos(a), math.sin(a)
        x = w * math.copysign(abs(c) ** (2 / p), c)
        y = h * math.copysign(abs(s) ** (2 / p), s)
        if bottom_flat and y < 0:
            y *= 0.35
        pts.append((cx + x, cy + y))
    return pts


def smooth(z, pts):
    """Piecewise-smooth curve through (z, value) points."""
    zs, vs = zip(*pts)
    return float(np.interp(z, zs, vs))


def body_top(z, length, low, hood, deck, belt, cabin):
    """Height of the body's top line (bumper, hood, belt line, deck) at z."""
    L = length / 2
    return smooth(z, [(-L, deck * 0.86), (-L * 0.82, deck), (cabin[0], belt), (cabin[1], belt), (L * 0.55, hood), (L, low + 0.18)])


def car_body(length, width, low, hood, deck, belt, roof, cabin, stations=36, ring=28):
    """Body (lower part up to the belt / hood / deck line) and greenhouse (glass cabin) meshes."""
    L = length / 2
    body_rings, glass_rings = [], []
    c0, c1 = cabin
    for i in range(stations):
        z = -L + length * i / (stations - 1)
        t = abs(z) / L
        w = width / 2 * (1 - 0.18 * t ** 4)
        top = body_top(z, length, low, hood, deck, belt, cabin)
        bottom = 0.16 + 0.05 * t ** 3
        cy, hh = (top + bottom) / 2, (top - bottom) / 2
        body_rings.append(np.array([(x, y, z) for x, y in superellipse(0, cy, w, hh, ring)]))
    for i in range(stations // 2):
        z = c0 + (c1 - c0) * i / (stations // 2 - 1)
        u = (z - c0) / (c1 - c0)
        rise = math.sin(math.pi * min(1, u * 1.15)) ** 0.6  # leans back like a windscreen
        h = max(0.012, (roof - belt) * rise)
        w = width / 2 * 0.80
        ring_pts = [(x, y, z) for x, y in superellipse(0, belt + h / 2 - 0.02, w * (0.9 + 0.1 * rise), h / 2 + 0.02, ring, 2.6, False)]
        glass_rings.append(np.array(ring_pts))
    return loft(body_rings, ring), loft(glass_rings, ring)


def cylinder(radius, width, segments=28, axis="x"):
    """A closed cylinder along X (wheels) or Z (steering wheel), centred at the origin."""
    pos, tri, uv = [], [], []
    for side in (-1, 1):
        for k in range(segments):
            a = 2 * math.pi * k / segments
            pos.append((side * width / 2, radius * math.cos(a), radius * math.sin(a)))
            uv.append((k / segments, (side + 1) / 2))
    for k in range(segments):
        a, b = k, (k + 1) % segments
        tri += [(a, b, segments + a), (b, segments + b, segments + a)]
    for side, base in ((-1, 0), (1, segments)):
        centre = len(pos)
        pos.append((side * width / 2, 0, 0))
        uv.append((0.5, 0.5))
        for k in range(segments):
            a, b = base + k, base + (k + 1) % segments
            tri.append((centre, a, b) if side > 0 else (centre, b, a))
    pos = np.array(pos)
    if axis == "z":
        pos = pos[:, [1, 2, 0]]
    return pos, tri, uv


def box(cx, cy, cz, sx, sy, sz):
    v, i = fx.box_geometry(sx, sy, sz, centre=(cx, cy, cz))
    return v, i


# ---------------------------------------------------------------- textures


def font(size):
    for name in ("bahnschrift.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def livery(base, stripe, number, sponsor, size=512):
    """Paint texture: u runs along the car, v around it."""
    img = Image.new("RGB", (size, size), base)
    d = ImageDraw.Draw(img)
    # two stripes over the top (around v = 0.25)
    for off in (-0.045, 0.045):
        y = int((0.25 + off) * size)
        d.rectangle([0, y - 9, size, y + 9], fill=stripe)
    # number roundels on both sides (v = 0 and 0.5)
    for v in (0.0, 0.5, 1.0):
        cx, cy = int(0.47 * size), int(v * size)
        d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=(245, 245, 240))
        d.text((cx, cy), str(number), fill=(20, 20, 20), font=font(52), anchor="mm")
        d.text((int(0.72 * size), cy), sponsor, fill=stripe, font=font(28), anchor="mm")
    # darker sills (v ~ 0.75 is the underside)
    d.rectangle([0, int(0.66 * size), size, int(0.84 * size)], fill=tuple(int(c * 0.35) for c in base))
    return img


def solid(color, size=16):
    return Image.new("RGB", (size, size), color)


def tyre_texture(size=128):
    img = Image.new("RGB", (size, size), (24, 24, 26))
    d = ImageDraw.Draw(img)
    for x in range(0, size, 8):
        d.line([x, 0, x, size], fill=(34, 34, 36), width=3)
    return img


def png(img):
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def jpg(img):
    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()


# ---------------------------------------------------------------- cars

CARS = [
    dict(id="vel_corsa_gt3", name="Corsa Rossa GT3", brand="Velocità", year=2023, cls="GT3", country="Italy",
         author="Demo Garage", length=4.6, width=2.0, low=0.3, hood=0.82, deck=0.95, belt=0.9, roof=1.22,
         cabin=(-0.7, 0.9), bhp=550, torque=600, weight=1260, top=290, acc="3.3", lod=True, anims=True,
         skins=[("red_17", (196, 18, 32), (250, 250, 250), 17, "FORZA"), ("black_3", (22, 22, 24), (255, 196, 0), 3, "NOTTE"),
                ("white_88", (235, 235, 230), (196, 18, 32), 88, "BIANCO")]),
    dict(id="kes_nordlicht_rs", name="Nordlicht RS", brand="Kessler", year=2019, cls="Street", country="Germany",
         author="Demo Garage", length=4.4, width=1.86, low=0.32, hood=0.86, deck=1.0, belt=0.98, roof=1.36,
         cabin=(-0.9, 0.75), bhp=420, torque=520, weight=1480, top=270, acc="4.1", csp=True,
         skins=[("silver", (170, 176, 184), (30, 60, 140), 7, "NORD"), ("blue", (25, 70, 160), (240, 240, 240), 12, "NORD")]),
    dict(id="lum_aurora_spyder", name="Aurora Spyder", brand="Lumen", year=2024, cls="Hypercar", country="Sweden",
         author="Demo Garage", length=4.7, width=2.05, low=0.26, hood=0.74, deck=0.9, belt=0.82, roof=1.12,
         cabin=(-0.5, 0.75), bhp=980, torque=1100, weight=1380, top=380, acc="2.4", lod=True,
         skins=[("ice", (200, 225, 240), (40, 110, 220), 1, "AURORA"), ("ember", (230, 90, 30), (25, 25, 25), 9, "EMBER")]),
    dict(id="fjo_bluebird_rally", name="Bluebird Rally", brand="Fjord", year=1998, cls="Rally", country="Norway",
         author="Demo Garage", length=4.1, width=1.8, low=0.38, hood=0.98, deck=1.05, belt=1.05, roof=1.45,
         cabin=(-1.0, 0.55), bhp=300, torque=480, weight=1230, top=210, acc="4.6", anims=True,
         skins=[("works", (30, 80, 190), (250, 210, 0), 5, "FJORD"), ("privateer", (240, 240, 235), (20, 120, 60), 21, "SNØ")]),
    dict(id="hos_sakura_drift", name="Sakura Drift", brand="Hoshi", year=2001, cls="Drift", country="Japan",
         author="Demo Garage", length=4.45, width=1.75, low=0.34, hood=0.9, deck=1.0, belt=0.98, roof=1.32,
         cabin=(-0.95, 0.65), bhp=380, torque=420, weight=1240, top=250, acc="5.0",
         skins=[("pink", (230, 120, 170), (30, 30, 30), 86, "SAKURA"), ("green", (40, 160, 90), (240, 240, 240), 13, "HOSHI")]),
    dict(id="tem_tempest_lmp", name="Tempest LMP", brand="Tempest", year=2022, cls="Prototype", country="UK",
         author="Demo Garage", length=4.9, width=2.0, low=0.22, hood=0.7, deck=0.96, belt=0.78, roof=1.04,
         cabin=(-0.2, 0.9), bhp=680, torque=650, weight=1030, top=340, acc="2.9", csp=True,
         skins=[("orange", (245, 120, 20), (20, 20, 30), 36, "TEMPEST"), ("teal", (20, 150, 150), (245, 245, 245), 7, "TIDE")]),
]


def build_car(spec, folder):
    car = folder / spec["id"]
    (car / "ui").mkdir(parents=True, exist_ok=True)
    (body, glass) = car_body(spec["length"], spec["width"], spec["low"], spec["hood"], spec["deck"], spec["belt"],
                             spec["roof"], spec["cabin"])
    mats = [
        fx.material("EXT_Carpaint", "ksPerPixelMultiMap", [("txDiffuse", "body.dds")],
                    props=[("ksSpecularEXP", 120, (0, 0, 0)), ("fresnelMaxLevel", 0.6, (0, 0, 0))]),
        fx.material("Glass_windows", "ksWindscreen", [("txDiffuse", "glass.png")], blend=1,
                    props=[("ksSpecularEXP", 200, (0, 0, 0))]),
        fx.material("Tyre", "ksTyres", [("txDiffuse", "tyre.png")], props=[("ksSpecularEXP", 8, (0, 0, 0))]),
        fx.material("Rim_alloy", "ksPerPixel", [("txDiffuse", "rim.png")], props=[("ksSpecularEXP", 60, (0, 0, 0))]),
        fx.material("Headlight", "ksPerPixel", [("txDiffuse", "light.png")]),
        fx.material("Taillight", "ksPerPixel", [("txDiffuse", "tail.png")]),
        fx.material("Carbon_splitter", "ksPerPixel", [("txDiffuse", "carbon.png")], props=[("ksSpecularEXP", 90, (0, 0, 0))]),
        fx.material("Interior_dark", "ksPerPixel", [("txDiffuse", "interior.png")]),
        fx.material("Chrome_exhaust", "ksPerPixel", [("txDiffuse", "rim.png")], props=[("ksSpecularEXP", 180, (0, 0, 0))]),
    ]
    top = lambda z: body_top(z, spec["length"], spec["low"], spec["hood"], spec["deck"], spec["belt"], spec["cabin"])  # noqa: E731
    L, W = spec["length"] / 2, spec["width"] / 2
    wheel_r, wheel_w = 0.34, 0.26
    axle = L * 0.62
    nodes = [fx.mesh("body_main", 0, *mesh_data(*body)), fx.mesh("glass_cabin", 1, *mesh_data(*glass))]
    for name, x, z in (("LF", W - 0.16, axle), ("RF", -W + 0.16, axle), ("LR", W - 0.16, -axle * 0.95), ("RR", -W + 0.16, -axle * 0.95)):
        tyre = fx.mesh("tyre_%s" % name, 2, *mesh_data(*cylinder(wheel_r, wheel_w)))
        rim = fx.mesh("rim_%s" % name, 3, *mesh_data(*cylinder(wheel_r * 0.68, wheel_w + 0.01, 20)))
        nodes.append(fx.dummy("WHEEL_%s" % name, [tyre, rim], (x, wheel_r, z)))
    zf, zr = L * 0.9, -L * 0.93
    for side in (1, -1):
        nodes.append(fx.mesh("headlight_%d" % side, 4, *box(side * W * 0.6, top(zf) - 0.035, zf, 0.15, 0.03, 0.07)))
        nodes.append(fx.mesh("taillight_%d" % side, 5, *box(side * W * 0.66, top(zr) - 0.06, zr - 0.06, 0.17, 0.035, 0.04)))
        nodes.append(fx.dummy("exhaust_%d" % side, [fx.mesh("exhaust_tip_%d" % side, 8, *mesh_data(*cylinder(0.045, 0.16, 16, axis="z")))],
                              (side * W * 0.3, 0.24, -L + 0.02)))
    nodes.append(fx.mesh("splitter", 6, *box(0, 0.17, L - 0.12, W * 0.88, 0.02, 0.14)))
    seat = fx.mesh("seat", 7, *box(W * 0.35, spec["belt"] - 0.32, spec["cabin"][0] * 0.3, 0.2, 0.18, 0.22))
    steer = fx.dummy("STEER_HR", [fx.mesh("steering_wheel", 7, *mesh_data(*cylinder(0.17, 0.03, 24, axis="z")))],
                     (W * 0.35, spec["belt"] + 0.05, spec["cabin"][0] * 0.3 + 0.45))
    nodes += [seat, steer]
    textures = {
        "body.dds": png(livery(*spec["skins"][0][1:])),
        "glass.png": png(Image.new("RGBA", (16, 16), (60, 78, 96, 90))),  # see-through tinted glass
        "tyre.png": png(tyre_texture()), "rim.png": png(solid((200, 202, 206))),
        "light.png": png(solid((235, 240, 250))), "tail.png": png(solid((200, 20, 20))),
        "carbon.png": png(carbon()), "interior.png": png(solid((28, 28, 30))),
    }
    data = fx.kn5_bytes(fx.dummy(spec["id"] + ".kn5", nodes), textures, mats)
    (car / (spec["id"] + ".kn5")).write_bytes(data)
    if spec.get("lod"):  # a lighter model for the Game-ready preset
        lod_nodes = [fx.mesh("body_lod", 0, *mesh_data(*car_body(spec["length"], spec["width"], spec["low"], spec["hood"],
                                                                    spec["deck"], spec["belt"], spec["roof"], spec["cabin"], 14, 12)[0]))]
        (car / (spec["id"] + "_lod_b.kn5")).write_bytes(fx.kn5_bytes(fx.dummy("lod", lod_nodes), {"body.dds": textures["body.dds"]}, mats[:1]))
    if spec.get("anims"):  # turns the steering wheel back and forth
        frames = []
        for i in range(31):
            a = math.radians(90 * math.sin(i / 30 * 2 * math.pi))
            frames.append([0, 0, math.sin(a / 2), math.cos(a / 2), W * 0.35, spec["belt"] + 0.05,
                           spec["cabin"][0] * 0.3 + 0.45, 1, 1, 1])
        (car / "animations").mkdir(exist_ok=True)
        (car / "animations" / "steer_demo.ksanim").write_bytes(fx.ksanim_bytes({"STEER_HR": frames}))
    for skin_id, base, stripe, number, sponsor in spec["skins"]:
        d = car / "skins" / skin_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "body.png").write_bytes(png(livery(base, stripe, number, sponsor)))
        (d / "ui_skin.json").write_text(json.dumps({"skinname": skin_id.replace("_", " ").title(), "drivername": DRIVERS[number % len(DRIVERS)],
                                                   "team": spec["brand"] + " Racing", "number": str(number)}), encoding="utf-8")
        preview = d / "preview.jpg"
        if not preview.exists():
            (preview).write_bytes(jpg(placeholder_preview(base, stripe, spec["name"])))
    hp = spec["bhp"]
    curve = [[r, round(hp * min(1, (r / 7000) ** 0.9) * (1 - max(0, r - 7500) / 6000))] for r in range(1000, 8600, 500)]
    tq = [[r, round(spec["torque"] * (0.65 + 0.35 * math.sin(min(math.pi, r / 9000 * math.pi * 1.1))))] for r in range(1000, 8600, 500)]
    ui = {"name": spec["name"], "brand": spec["brand"], "year": spec["year"], "class": spec["cls"], "country": spec["country"],
          "author": spec["author"], "version": "1.0", "url": "https://example.com/kn5-garage-demo",
          "description": "A fictional demo car made by KN5 Garage's demo generator.\nProcedural body, wheels, lights and liveries.",
          "tags": ["demo", spec["cls"].lower(), "#" + spec["country"]],
          "specs": {"bhp": "%d bhp" % hp, "torque": "%d Nm" % spec["torque"], "weight": "%d kg" % spec["weight"],
                    "topspeed": "%d km/h" % spec["top"], "acceleration": "%s s" % spec["acc"],
                    "pwratio": "%.2f kg/hp" % (spec["weight"] / hp)},
          "powerCurve": curve, "torqueCurve": tq}
    (car / "ui" / "ui_car.json").write_text(json.dumps(ui, indent=2, ensure_ascii=False), encoding="utf-8")
    (car / "ui" / "badge.png").write_bytes(png(badge(spec["brand"])))
    if spec.get("csp"):
        (car / "extension").mkdir(exist_ok=True)
        (car / "extension" / "ext_config.ini").write_text(
            "[ABOUT]\nAUTHOR=Demo Garage\n[LIGHT_HEADLIGHT_0]\nCOLOR=1,1,1,10\n[EMISSIVE_BRAKE_0]\nNAME=taillight_1\n"
            "[RAIN_FX]\nGLASS=glass_cabin\n[REAL_MIRROR_0]\n", encoding="utf-8")
        (car / "data").mkdir(exist_ok=True)
        (car / "data" / "car.ini").write_text("[BASIC]\nTOTALMASS=%d\n[FUEL]\nMAX_FUEL=80\n" % spec["weight"])
        (car / "data" / "drivetrain.ini").write_text("[TRACTION]\nTYPE=RWD\n[GEARS]\nCOUNT=6\n")
        (car / "data" / "engine.ini").write_text("[HEADER]\nPOWER_CURVE=power.lut\n[ENGINE_DATA]\nLIMITER=8200\n"
                                                 "[TURBO_0]\nMAX_BOOST=1.2\n")
        (car / "data" / "power.lut").write_text("".join(  # torque (Nm) before boost, by rpm
            "%d|%d\n" % (r, round(spec["torque"] / 2.2 * f))
            for r, f in ((1000, .55), (3000, .85), (5000, 1), (7000, .9), (8200, .75))))
    return car


DRIVERS = ["A. Rossi", "M. Keller", "S. Lindqvist", "J. Moreau", "K. Tanaka", "L. García", "E. Novak", "P. Silva"]


def carbon(size=64):
    img = Image.new("RGB", (size, size), (20, 20, 22))
    d = ImageDraw.Draw(img)
    for y in range(0, size, 8):
        for x in range(0, size, 8):
            d.rectangle([x, y, x + 3, y + 3], fill=(44, 44, 48) if (x + y) % 16 else (32, 32, 36))
    return img


def badge(brand, size=128):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([6, 6, size - 6, size - 6], fill=(18, 20, 24, 255), outline=(255, 77, 46, 255), width=8)
    d.text((size // 2, size // 2), brand[0], fill=(255, 255, 255, 255), font=font(64), anchor="mm")
    return img


def placeholder_preview(base, stripe, name, size=(640, 360)):
    """Replaced by real 3D renders when tools/screenshots.py runs."""
    img = Image.new("RGB", size, (20, 24, 29))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([110, 150, 530, 260], 50, fill=base)
    d.rectangle([110, 196, 530, 204], fill=stripe)
    for x in (200, 440):
        d.ellipse([x - 40, 220, x + 40, 300], fill=(15, 15, 15))
    d.text((320, 60), name, fill=(230, 230, 230), font=font(34), anchor="mm")
    return img


WALKER_BVH = """HIERARCHY
ROOT Hips
{
  OFFSET 0 95 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
  JOINT Spine { OFFSET 0 22 0 CHANNELS 3 Zrotation Xrotation Yrotation
    JOINT Neck { OFFSET 0 30 0 CHANNELS 3 Zrotation Xrotation Yrotation
      JOINT Head { OFFSET 0 10 0 CHANNELS 3 Zrotation Xrotation Yrotation End Site { OFFSET 0 18 0 } } }
    JOINT LeftArm { OFFSET 17 28 0 CHANNELS 3 Zrotation Xrotation Yrotation
      JOINT LeftForeArm { OFFSET 0 -28 0 CHANNELS 3 Zrotation Xrotation Yrotation End Site { OFFSET 0 -25 0 } } }
    JOINT RightArm { OFFSET -17 28 0 CHANNELS 3 Zrotation Xrotation Yrotation
      JOINT RightForeArm { OFFSET 0 -28 0 CHANNELS 3 Zrotation Xrotation Yrotation End Site { OFFSET 0 -25 0 } } } }
  JOINT LeftUpLeg { OFFSET 10 0 0 CHANNELS 3 Zrotation Xrotation Yrotation
    JOINT LeftLeg { OFFSET 0 -45 0 CHANNELS 3 Zrotation Xrotation Yrotation End Site { OFFSET 0 -45 0 } } }
  JOINT RightUpLeg { OFFSET -10 0 0 CHANNELS 3 Zrotation Xrotation Yrotation
    JOINT RightLeg { OFFSET 0 -45 0 CHANNELS 3 Zrotation Xrotation Yrotation End Site { OFFSET 0 -45 0 } } }
}
MOTION
"""


def walker_motion(frames=40):
    """A simple walk: legs and arms swing, the body moves forward."""
    import math
    lines = ["Frames: %d" % frames, "Frame Time: 0.0333333"]
    for f in range(frames):
        a = math.sin(f / frames * 2 * math.pi)
        knee = max(0.0, -math.cos(f / frames * 2 * math.pi)) * 35
        row = [0, 95 + abs(a) * 2, f * 2.5, 0, 0, 0,      # hips
               0, 4, 0, 0, 0, 0, 0, -4, 0,                 # spine, neck, head
               8, -25 * a, 0, 0, -20, 0,                    # left arm, forearm
               -8, 25 * a, 0, 0, -20, 0,                    # right arm, forearm
               0, 28 * a, 0, 0, knee, 0,                    # left leg
               0, -28 * a, 0, 0, 35 - knee, 0]              # right leg
        lines.append(" ".join("%.3f" % v for v in row))
    return "\n".join(lines) + "\n"


def build_figures(folder):
    """Loose 3D files next to the cars: a motion-capture walker and a figure to 3D print."""
    figs = Path(folder) / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    (figs / "walker.bvh").write_text(WALKER_BVH + walker_motion(), encoding="utf-8")
    tris = []
    for w, h0, h1 in ((20, 0, 8), (14, 8, 16), (8, 16, 24), (3, 24, 40)):
        c = [(-w, h0, -w), (w, h0, -w), (w, h0, w), (-w, h0, w), (-w, h1, -w), (w, h1, -w), (w, h1, w), (-w, h1, w)]
        for a, b, d, e in ((0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (4, 5, 6, 7), (3, 2, 1, 0)):
            tris += [(c[a], c[b], c[d]), (c[a], c[d], c[e])]
    lines = ["solid tower"]
    for t in tris:
        lines.append("facet normal 0 0 0\nouter loop\n" + "".join("vertex %g %g %g\n" % v for v in t)
                     + "endloop\nendfacet")
    (figs / "tower.stl").write_text("\n".join(lines) + "\nendsolid tower\n", encoding="utf-8")
    return figs


def main(argv=None):
    folder = Path((argv or sys.argv[1:] or [str(ROOT / ".dev" / "demo_cars")])[0])
    folder.mkdir(parents=True, exist_ok=True)
    for spec in CARS:
        build_car(spec, folder)
        print("built", spec["id"])
    # an identical copy under another name, so duplicates show up in the demo
    import shutil
    copy = folder / "vel_corsa_gt3_old_copy"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(folder / "vel_corsa_gt3", copy)
    ui = json.loads((copy / "ui" / "ui_car.json").read_text(encoding="utf-8"))
    ui["name"] += " (old download)"
    (copy / "ui" / "ui_car.json").write_text(json.dumps(ui, indent=2, ensure_ascii=False), encoding="utf-8")
    build_figures(folder)
    print("demo garage ready in", folder)
    return folder


if __name__ == "__main__":
    main()
