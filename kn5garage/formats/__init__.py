"""Every 3D format the app reads, behind one function: load(path) -> a kn5-like model.

Readers live next to this file (simple.py: STL, PLY, 3DS; collada.py: DAE; fbx.py: FBX;
characters.py: BVH, PMX; sketchup.py: SKP); glTF / GLB / VRM and OBJ are in importer.py, .kn5 in kn5.py."""

import struct
import zlib
from pathlib import Path

# extension -> (what it is, reader module, function); readers are imported when first used
FORMATS = {
    ".kn5": ("Assetto Corsa model", None, None),
    ".glb": ("glTF binary", "kn5garage.importer", "load_gltf"),
    ".gltf": ("glTF", "kn5garage.importer", "load_gltf"),
    ".vrm": ("VRM avatar", "kn5garage.importer", "load_gltf"),
    ".obj": ("Wavefront OBJ", "kn5garage.importer", "load_obj"),
    ".fbx": ("Autodesk FBX", "kn5garage.formats.fbx", "load_fbx"),
    ".dae": ("COLLADA", "kn5garage.formats.collada", "load_dae"),
    ".stl": ("STL (3D printing)", "kn5garage.formats.simple", "load_stl"),
    ".ply": ("PLY (scans)", "kn5garage.formats.simple", "load_ply"),
    ".3ds": ("3D Studio", "kn5garage.formats.simple", "load_3ds"),
    ".bvh": ("BVH motion capture", "kn5garage.formats.characters", "load_bvh"),
    ".pmx": ("MikuMikuDance PMX", "kn5garage.formats.characters", "load_pmx"),
    ".skp": ("SketchUp model", "kn5garage.formats.sketchup", "load_skp"),
    ".skb": ("SketchUp backup", "kn5garage.formats.sketchup", "load_skp"),
}
# known but unreadable: what to tell the user
UNSUPPORTED = {
    ".blend": "a Blender file: open it in Blender and export .glb or .fbx",
    ".max": "a 3ds Max scene: export it from 3ds Max as .fbx",
    ".ma": "a Maya scene: export it from Maya as .fbx",
    ".mb": "a Maya scene: export it from Maya as .fbx",
    ".pmd": "an old MikuMikuDance model: save it as .pmx in PMX Editor",
    ".gmt": "an rFactor model inside the game's own packing; export it with the game's modding tools",
    ".modelbin": "a Forza model: Forza's game files are encrypted and can't be opened",
    ".unity3d": "a Unity asset bundle: game bundles are often encrypted; use the game's own mod tools",
}
EXTENSIONS = tuple(sorted(FORMATS))


def describe(path):
    return FORMATS.get(Path(path).suffix.lower(), ("", None, None))[0]


def load(path, blender_exe=None):
    from importlib import import_module

    from ..importer import MAX_FILE, ImportError3D
    from ..kn5 import Kn5Error, read_kn5
    path = Path(path)
    ext = path.suffix.lower()
    if ext in UNSUPPORTED:
        raise ImportError3D("%s is %s." % (path.name, UNSUPPORTED[ext]))
    if ext not in FORMATS:
        raise ImportError3D("%s files can't be opened (supported: %s)" % (ext or "these", ", ".join(EXTENSIONS)))
    if not path.is_file():
        raise ImportError3D("file not found")
    if path.stat().st_size > MAX_FILE:
        raise ImportError3D("the file is larger than 1 GB")
    if ext == ".kn5":
        return read_kn5(path)
    _, module, fn = FORMATS[ext]
    try:
        return getattr(import_module(module), fn)(path)
    except Kn5Error as e:
        if ext == ".fbx" and blender_exe and ("text (ASCII)" in str(e) or "too old" in str(e)):
            return _fbx_through_blender(path, blender_exe)
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, MemoryError,
            struct.error, zlib.error, EOFError, RecursionError) as e:
        raise ImportError3D("damaged %s file (%s)" % (ext, type(e).__name__))


def _fbx_through_blender(path, blender_exe):
    import tempfile

    from ..blender import fbx_to_glb
    from ..importer import load_gltf
    with tempfile.TemporaryDirectory() as t:
        glb = Path(t) / (path.stem + ".glb")
        fbx_to_glb(blender_exe, path, glb)
        return load_gltf(glb)
