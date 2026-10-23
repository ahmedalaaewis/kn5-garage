"""
Send exported cars to Blender: find Blender on this PC, write a scene-setup script
next to the .glb, and open Blender with it.

The script imports the car, sorts its parts into collections (Body, Wheels, Interior,
Glass, Lights), adds a ground, studio lights and a camera, and saves a .blend file next
to the .glb. It also works on its own: in Blender, Scripting > Open > Run Script.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def candidates():
    """Likely places for blender.exe, newest version first."""
    found = []
    exe = "blender.exe" if sys.platform == "win32" else "blender"
    on_path = shutil.which("blender")
    if on_path:
        found.append(Path(on_path))
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        for root in roots:
            base = Path(root) / "Blender Foundation"
            if base.is_dir():
                found += sorted(base.glob("Blender*/" + exe), key=_version_key, reverse=True)
        for lib in steam_libraries():
            found.append(lib / "steamapps" / "common" / "Blender" / exe)
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Blender Foundation"
        if local.is_dir():
            found += sorted(local.glob("Blender*/" + exe), key=_version_key, reverse=True)
    elif sys.platform == "darwin":
        found.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    return found


def _version_key(p):
    nums = re.findall(r"\d+", p.parent.name)
    return [int(n) for n in nums] or [0]


def steam_libraries():
    vdf = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "libraryfolders.vdf"
    libs = [vdf.parent.parent]
    try:
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return libs


def looks_like_blender(path):
    """Only programs named blender / blender.exe (or the macOS app binary) are ever launched."""
    p = Path(path)
    return p.is_file() and p.name.lower() in ("blender", "blender.exe")


def find_blender(saved=None):
    """Path of blender(.exe) or None. A path the user picked wins."""
    for p in ([Path(saved)] if saved else []) + candidates():
        if p and looks_like_blender(p):
            return p
    return None


SCRIPT = r'''# KN5 Garage: Blender scene setup for {name}
# Run inside Blender (Scripting > Open > Run Script), or let KN5 Garage open Blender with it.
import math
import os
import re

import bpy
from mathutils import Vector

GLB_NAME = {glb_name}


def find_glb(name):
    """The .glb sits next to this script (run with --python, or opened in the Text Editor)."""
    dirs = []
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    dirs += [os.path.dirname(bpy.path.abspath(t.filepath)) for t in bpy.data.texts if t.filepath]
    for d in dirs:
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    raise SystemExit("KN5 Garage: keep this script in the same folder as " + name)


GLB = find_glb(GLB_NAME)
BLEND = os.path.splitext(GLB)[0] + ".blend"

for obj in list(bpy.data.objects):  # start from an empty scene
    bpy.data.objects.remove(obj, do_unlink=True)

before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=GLB)
car = [o for o in bpy.data.objects if o not in before]
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"

GROUPS = [
    ("Wheels", r"wheel|tyre|tire|rim|disc|rotor|brake|caliper|susp|hub"),
    ("Interior", r"cockpit|steer|seat|int_|interior|dash|pedal|gear|shifter|belt|gauge|display"),
    ("Lights", r"light|lamp|lens|headl|taill|indicator|blinker"),
]


def chain(o):
    names = []
    while o:
        names.append(o.name)
        o = o.parent
    return " ".join(names)


def is_glass(o):
    for slot in getattr(o, "material_slots", []):
        m = slot.material
        if m and (getattr(m, "blend_method", "OPAQUE") == "BLEND" or re.search(r"glass|window|windscreen", m.name, re.I)):
            return True
    return False


root = bpy.data.collections.new("{name}")
scene.collection.children.link(root)
cols = {{}}
for title in ("Body", "Wheels", "Interior", "Glass", "Lights", "Rig"):
    c = bpy.data.collections.new(title)
    root.children.link(c)
    cols[title] = c

for o in car:
    if o.type == "MESH":
        names = chain(o)
        group = "Glass" if is_glass(o) else next((t for t, pat in GROUPS if re.search(pat, names, re.I)), "Body")
    else:
        group = "Rig"  # empties: wheel / steering / door pivots
    for c in list(o.users_collection):
        c.objects.unlink(o)
    cols[group].objects.link(o)

meshes = [o for o in car if o.type == "MESH"]
corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
hi = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
centre, size = (lo + hi) / 2, (hi - lo).length

studio = bpy.data.collections.new("Studio")
scene.collection.children.link(studio)


def add(obj):
    studio.objects.link(obj)
    return obj


bpy.ops.mesh.primitive_plane_add(size=size * 6, location=(centre.x, centre.y, lo.z))
ground = bpy.context.active_object
ground.name = "Ground"
for c in list(ground.users_collection):
    c.objects.unlink(ground)
studio.objects.link(ground)
mat = bpy.data.materials.new("Ground")
mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.05, 0.05, 0.055, 1)
mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.6
ground.data.materials.append(mat)

for name, offset, energy, size_l in (("Key", (1.2, -1.0, 1.4), 2000, 3), ("Fill", (-1.4, -0.6, 0.9), 700, 4),
                                     ("Rim", (0.0, 1.5, 1.2), 1200, 3)):
    light = bpy.data.lights.new(name, "AREA")
    light.energy = energy * (size / 5) ** 2
    light.size = size_l * size / 5
    obj = add(bpy.data.objects.new(name, light))
    obj.location = centre + Vector(offset) * size
    obj.rotation_euler = (centre - obj.location).to_track_quat("-Z", "Y").to_euler()

cam_data = bpy.data.cameras.new("Camera")
cam_data.lens = 50
cam = add(bpy.data.objects.new("Camera", cam_data))
cam.location = centre + Vector((0.9, -1.3, 0.35)) * size
cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam

world = scene.world or bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    bg.inputs[0].default_value = (0.18, 0.19, 0.21, 1)
    bg.inputs[1].default_value = 0.6

for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        pass
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080

bpy.ops.wm.save_as_mainfile(filepath=BLEND)
print("KN5 Garage: scene ready, saved", BLEND)
'''


def write_setup_script(glb_path, name=None):
    """The script looks for the .glb next to itself, so it holds no absolute paths and
    still works after the files are moved or shared."""
    glb_path = Path(glb_path).resolve()
    script = glb_path.with_name(glb_path.stem + "_blender_setup.py")
    safe = re.sub(r"[^A-Za-z0-9_ \-]", "_", name or glb_path.stem)
    # json.dumps gives a valid Python string literal for any file name
    script.write_text(SCRIPT.format(glb_name=json.dumps(glb_path.name), name=safe), encoding="utf-8")
    return script


FBX_EXPR = """
import sys, bpy
glb, fbx = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
bpy.ops.export_scene.fbx(filepath=fbx, path_mode="COPY", embed_textures=True, bake_anim=True, add_leaf_bones=False)
"""


def glb_to_fbx(blender_exe, glb, fbx, timeout=900):
    """Convert a .glb to .fbx with Blender in the background (textures embedded)."""
    from .kn5 import Kn5Error
    if not looks_like_blender(blender_exe):
        raise Kn5Error("FBX export needs Blender: locate blender.exe first")
    try:
        p = subprocess.run([str(blender_exe), "--background", "--factory-startup", "--python-expr", FBX_EXPR,
                            "--", str(glb), str(fbx)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Kn5Error("Blender took too long to write the FBX")
    if p.returncode != 0 or not Path(fbx).is_file():
        lines = [line for line in (p.stderr or p.stdout or "").splitlines() if line.strip()]
        raise Kn5Error("Blender couldn't write the FBX%s" % (": " + lines[-1][:200] if lines else ""))


GLB_EXPR = """
import sys, bpy
fbx, glb = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=fbx)
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB")
"""


def fbx_to_glb(blender_exe, fbx, glb, timeout=900):
    """Read an .fbx with Blender in the background and write it as .glb."""
    from .kn5 import Kn5Error
    if not looks_like_blender(blender_exe):
        raise Kn5Error("FBX files are read through Blender: locate blender.exe first")
    try:
        p = subprocess.run([str(blender_exe), "--background", "--factory-startup", "--python-expr", GLB_EXPR,
                            "--", str(fbx), str(glb)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Kn5Error("Blender took too long to read the FBX")
    if p.returncode != 0 or not Path(glb).is_file():
        lines = [line for line in (p.stderr or p.stdout or "").splitlines() if line.strip()]
        raise Kn5Error("Blender couldn't read the FBX%s" % (": " + lines[-1][:200] if lines else ""))


def launch(blender_exe, script):
    """Open Blender with the setup script (doesn't wait for it)."""
    return subprocess.Popen([str(blender_exe), "--python", str(script)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
