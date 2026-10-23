"""Command line: convert one car folder, or every car in a folder."""

import argparse
import time
from pathlib import Path

from . import __version__, paths
from .blender import find_blender, glb_to_fbx
from .carfiles import find_cars
from .convert import PRESETS, TEXTURE_SIZES, convert_car
from .dialogs import pick_folder

PROJECT_DIR = paths.data_dir()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="KN5Garage convert", description="Convert Assetto Corsa .kn5 car models to .glb")
    ap.add_argument("folder", nargs="?", help="a car folder, or a folder containing car folders")
    ap.add_argument("-o", "--out", help="output folder (default: 'output' in the program folder)")
    ap.add_argument("--skin", help="skin folder name to use (default: first one; 'none' = kn5 textures only)")
    ap.add_argument("--keep-normal-green", action="store_true",
                    help="don't invert the green channel of normal maps")
    ap.add_argument("--preset", default="render", choices=[k for k in PRESETS if k != "preview"],
                    help="render (full quality), game (low-poly LOD, 1K textures), print (STL), obj, "
                         "or fbx (needs Blender)")
    ap.add_argument("--paint", help="paint colour for the car body, e.g. #c8102e")
    ap.add_argument("--paint-mode", default="tint", choices=("tint", "solid"))
    ap.add_argument("--texture-size", type=int, choices=TEXTURE_SIZES, help="largest texture side in pixels")
    ap.add_argument("--jpeg", type=int, metavar="QUALITY", help="store colour textures as JPEG (50-100)")
    ap.add_argument("--lights-on", action="store_true", help="make headlights and tail lights glow")
    ap.add_argument("--version", action="version", version="KN5 Garage %s" % __version__)
    args = ap.parse_args(argv)
    fbx = None
    if args.preset == "fbx":
        exe = find_blender()
        if not exe:
            print("The FBX preset needs Blender, and it wasn't found.")
            return 1
        fbx = lambda glb, out: glb_to_fbx(exe, glb, out)  # noqa: E731

    interactive = args.folder is None
    folder = args.folder or pick_folder()
    if not folder:
        print("No folder chosen.")
        return 1
    folder = Path(folder)
    if not folder.is_dir():
        print("Folder not found: %s" % folder)
        return 1
    out_dir = Path(args.out) if args.out else PROJECT_DIR / "output"

    cars = find_cars(folder)
    if not cars:
        print("No .kn5 files found in %s or its subfolders." % folder)
    ok = 0
    for i, car in enumerate(cars, 1):
        print("[%d/%d] %s" % (i, len(cars), car.name))
        t = time.time()
        try:
            out = convert_car(car, out_dir, args.skin, flip_green=not args.keep_normal_green, preset=args.preset,
                              paint=args.paint, paint_mode=args.paint_mode, texture_size=args.texture_size,
                              jpeg_quality=args.jpeg, lights_on=args.lights_on, fbx_converter=fbx)["path"]
            print("  -> %s (%.1f MB, %.1fs)" % (out, out.stat().st_size / 1e6, time.time() - t))
            ok += 1
        except Exception as e:
            print("  ! failed: %s" % e)
    print("\nDone: %d of %d converted. Files are in %s" % (ok, len(cars), out_dir))
    if interactive:
        input("Press Enter to close...")
    return 0 if ok == len(cars) else 1
