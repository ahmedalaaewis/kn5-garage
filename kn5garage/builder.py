"""Making an Assetto Corsa car from a 3D model (beta).

The new car is a copy of a donor car: its physics, sounds, wheels, driver and cockpit stay,
and its body is swapped for the 3D model, scaled and placed where the donor's body was.
Wheels in the 3D model stay part of the body (they won't turn); the donor's wheels do."""

import re

import numpy as np

from . import carfiles, importer, kn5write, parts
from .edits import EditError

# parts of the donor that are kept: they move, or the game looks for them by name
KEEP = re.compile(r"^(WHEEL_|SUSP_|HUB_|DISC_|RIM_|TYRE_|STEER|COCKPIT_|DRIVER|CINTURE|SHIFT|FLYWHEEL|ARM_|"
                  r"DAMAGE_|DOOR_|WING_|REAR_WING|HOOD_|POSITION_|EXHAUST|DUMMY)", re.I)
ROTATIONS = (0, 90, 180, 270)


def body_parts(model):
    """Indexes of the donor's body meshes: not under a kept part, not rigged."""
    out = []

    def visit(n, kept):
        kept = kept or (n["cls"] == 1 and bool(KEEP.match(n["name"])))
        if n["cls"] == 2 and not kept and len(n["verts"]):
            out.append(index[id(n)])
        for c in n["children"]:
            visit(c, kept)
    index = {id(n): i for i, n in enumerate(parts.walk(model["root"]))}
    visit(model["root"], False)
    return out


def orient(model, rotate=0, z_up=False):
    """Turn an imported model so it stands like an AC car (Y up), rotated round Y by `rotate`°."""
    if rotate not in ROTATIONS:
        raise EditError("rotation must be 0, 90, 180 or 270")
    m = importer.rotation_y(rotate)
    if z_up:
        m = m @ np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], np.float64)
    root = model["root"]
    root["matrix"] = parts.flat(m @ parts.local_matrix(root))
    return model


def build(session, model, name, rotate=0, z_up=False):
    """Steps on a parts session (of the donor) that put the 3D model in place of its body."""
    body = body_parts(session.model())
    if not body:
        raise EditError("the donor car has no body parts to replace")
    key = session.add_source("build:" + name, name, orient(model, rotate, z_up))
    steps = [{"op": "remove", "node": i} for i in body]
    steps.append({"op": "add", "source": key, "node": 0, "replace": None, "fit": True, "fit_nodes": body})
    session.steps += steps
    session.redo = []
    session._rebuilt(reload=True)
    return len(body)


def write(session, target):
    """Write the new model as the car's main model and as every lower-detail model (LODs)."""
    data = kn5write.write_kn5(session.final_model())
    target.write(session.kn5_rel, data)
    main = (target.dir / session.kn5_rel).resolve()
    lods = {carfiles.find_lod_kn5(target.dir, level) for level in range(1, 5)} - {None}
    for f in sorted(lods):
        if f.resolve() != main:
            target.write(f.relative_to(target.dir).as_posix(), data)
    return len(data)
