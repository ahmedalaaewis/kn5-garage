"""Reading and editing a car's specs: the real physics in an unpacked data folder (weight,
engine curve, rev limit, turbo, fuel, final drive) and the specs shown in the game's menus
(ui/ui_car.json). Packed data.acd files are never decrypted."""

import json
from pathlib import Path

from .carinfo import read_ini, read_json_lenient
from .edits import EditError, ini_set, read_lut, write_lut

HP_PER_NM_RPM = 1 / 7121.0  # hp = torque (Nm) * rpm / 7121
UI_SPECS = ("bhp", "torque", "weight", "topspeed", "acceleration", "pwratio")
LIMITS = {"mass": (100, 10000), "fuel": (1, 1000), "limiter": (1000, 30000), "final": (1, 20), "boost": (0, 10),
          "scale": (0.1, 5)}


def _num(cfg, section, key):
    if cfg is None or not cfg.has_option(section, key):
        return None
    try:
        return float(cfg.get(section, key).split()[0])
    except (ValueError, IndexError):
        return None


def read_physics(car_dir):
    car_dir = Path(car_dir)
    data = car_dir / "data"
    packed = (car_dir / "data.acd").is_file()
    if not data.is_dir():
        return {"available": False, "packed": packed,
                "reason": "packed" if packed else "missing"}
    car, engine, drive = read_ini(data / "car.ini"), read_ini(data / "engine.ini"), read_ini(data / "drivetrain.ini")
    curve_file = (engine.get("HEADER", "POWER_CURVE").strip() if engine is not None and engine.has_option("HEADER", "POWER_CURVE")
                  else "power.lut")
    curve_path = data / Path(curve_file).name
    try:
        curve = read_lut(curve_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        curve = []
    turbos = []
    if engine is not None:
        for s in sorted(x for x in engine.sections() if x.upper().startswith("TURBO_")):
            turbos.append({"section": s, "boost": _num(engine, s, "MAX_BOOST")})
    return {
        "available": True, "packed": packed,  # packed + folder: the game uses data.acd until it's moved away
        "mass": _num(car, "BASIC", "TOTALMASS"), "fuel": _num(car, "FUEL", "MAX_FUEL"),
        "limiter": _num(engine, "ENGINE_DATA", "LIMITER"), "final": _num(drive, "GEARS", "FINAL"),
        "drive": drive.get("TRACTION", "TYPE").strip() if drive is not None and drive.has_option("TRACTION", "TYPE") else None,
        "turbos": turbos, "curveFile": curve_path.name, "curve": curve,
    }


def read_ui_specs(car_dir):
    ui = read_json_lenient(Path(car_dir) / "ui" / "ui_car.json")
    specs = ui.get("specs") if isinstance(ui.get("specs"), dict) else {}
    return {k: str(specs.get(k)) if specs.get(k) is not None else "" for k in UI_SPECS}


def _check(name, value, kind=None):
    lo, hi = LIMITS[kind or name]
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise EditError("%s must be a number" % name)
    if not lo <= v <= hi:
        raise EditError("%s must be between %g and %g" % (name, lo, hi))
    return v


def _fmt(v):
    return str(int(v)) if float(v).is_integer() else ("%.4f" % v).rstrip("0").rstrip(".")


def check_curve(curve):
    if not isinstance(curve, list) or not 2 <= len(curve) <= 200:
        raise EditError("the engine curve needs 2 to 200 points")
    out, last = [], -1.0
    for p in curve:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise EditError("each curve point is [rpm, torque]")
        rpm, nm = float(p[0]), float(p[1])
        if not (0 <= rpm <= 30000 and 0 <= nm <= 5000) or rpm <= last:
            raise EditError("curve points need rising rpm (0-30000) and torque 0-5000 Nm")
        out.append([rpm, nm])
        last = rpm
    return out


def apply_specs(target, physics=None, ui=None):
    """Write the changes through an edits.Target. physics: {mass, fuel, limiter, final, turbos:
    [{section, boost}], curve | scale}; ui: {specs: {...}, curvesFromPhysics: bool}. Returns notes."""
    notes = []
    car_dir = target.dir
    data = car_dir / "data"
    phys_now = read_physics(car_dir)
    curve = None
    if physics:
        if not phys_now["available"]:
            raise EditError("this car's physics are packed in data.acd; unpack its data folder to edit them")
        edits = {}  # file -> [(section, key, value)]
        if physics.get("mass") is not None:
            edits.setdefault("car.ini", []).append(("BASIC", "TOTALMASS", _fmt(_check("mass", physics["mass"]))))
        if physics.get("fuel") is not None:
            edits.setdefault("car.ini", []).append(("FUEL", "MAX_FUEL", _fmt(_check("fuel", physics["fuel"]))))
        if physics.get("limiter") is not None:
            edits.setdefault("engine.ini", []).append(("ENGINE_DATA", "LIMITER", _fmt(_check("limiter", physics["limiter"]))))
        if physics.get("final") is not None:
            edits.setdefault("drivetrain.ini", []).append(("GEARS", "FINAL", _fmt(_check("final", physics["final"]))))
        known = {t["section"].upper() for t in phys_now["turbos"]}
        for t in physics.get("turbos") or []:
            if str(t.get("section", "")).upper() not in known:
                raise EditError("unknown turbo section %s" % t.get("section"))
            edits.setdefault("engine.ini", []).append((t["section"], "MAX_BOOST", _fmt(_check("turbo boost", t["boost"], "boost"))))
        for fname, changes in edits.items():
            path = data / fname
            text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            for section, key, value in changes:
                text = ini_set(text, section, key, value)
            target.write("data/" + fname, text)
        if physics.get("curve") is not None:
            curve = check_curve(physics["curve"])
        elif physics.get("scale") is not None:
            scale = _check("power scale", physics["scale"], "scale")
            curve = [[r, round(n * scale, 2)] for r, n in phys_now["curve"]]
        if curve is not None:
            target.write("data/" + phys_now["curveFile"], write_lut(curve))
        if phys_now["packed"] and target.mode == "original":
            target.remove("data.acd")  # backed up; the game reads the edited data folder from now on
            notes.append("data.acd was moved to the backup so the game uses the edited data folder")
    if ui:
        ui_path = car_dir / "ui" / "ui_car.json"
        doc = read_json_lenient(ui_path)
        specs = doc.get("specs") if isinstance(doc.get("specs"), dict) else {}
        for k, v in (ui.get("specs") or {}).items():
            if k not in UI_SPECS:
                raise EditError("unknown spec %s" % k)
            v = str(v).strip()
            if len(v) > 40:
                raise EditError("%s is too long" % k)
            specs[k] = v
        doc["specs"] = specs
        if ui.get("curvesFromPhysics"):
            phys = read_physics(car_dir)  # after the physics edits above: new curve and boost
            if not phys.get("curve"):
                raise EditError("no engine curve to draw the menu charts from")
            boost = 1 + max([t["boost"] or 0 for t in phys.get("turbos", [])] + [0])
            doc["torqueCurve"] = [[r, round(n * boost, 1)] for r, n in phys["curve"]]
            doc["powerCurve"] = [[r, round(n * boost * r * HP_PER_NM_RPM, 1)] for r, n in phys["curve"]]
        target.write("ui/ui_car.json", json.dumps(doc, indent=2, ensure_ascii=False))
    return notes


def suggested_specs(physics):
    """Menu specs worked out from the physics: peak power and torque, weight, power to weight."""
    curve = physics.get("curve") or []
    boost = 1 + max([t["boost"] or 0 for t in physics.get("turbos", [])] + [0])
    out = {}
    if curve:
        hp = max(n * boost * r * HP_PER_NM_RPM for r, n in curve)
        nm = max(n * boost for r, n in curve)
        out["bhp"] = "%d bhp" % round(hp)
        out["torque"] = "%d Nm" % round(nm)
    if physics.get("mass"):
        out["weight"] = "%d kg" % round(physics["mass"])
        if curve:
            out["pwratio"] = "%.2f kg/hp" % (physics["mass"] / hp)
    return out
