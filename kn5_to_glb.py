"""
kn5_to_glb - convert Assetto Corsa car models (.kn5) to .glb (glTF 2.0).

Usage:
    python kn5_to_glb.py                      -> opens a folder picker
    python kn5_to_glb.py <folder> [options]   -> see --help

<folder> can be one car folder (contains a .kn5) or a folder full of car
folders, e.g. ...\\assettocorsa\\content\\cars, in which case every car is converted.
Car folders are only ever read, never modified.

The code lives in the kn5garage package; the names below are kept here so scripts
that `import kn5_to_glb` keep working.
"""

import sys

from kn5garage.carfiles import (find_cars, find_lod_kn5, find_main_kn5, is_secondary_kn5, list_skins,  # noqa: F401
                                load_animations, load_skin, output_name)
from kn5garage.cli import main
from kn5garage.convert import (PAINT_NAME, PRESETS, Converter, convert_car, count_meshes, fix_winding,  # noqa: F401
                               hex_rgb, is_paint_material, material_kind, srgb_to_linear, write_obj, write_stl)
from kn5garage.dialogs import pick_folder  # noqa: F401
from kn5garage.glb import GlbBuilder  # noqa: F401
from kn5garage.kn5 import (PLACEHOLDER_TEXTURE_SIZE, Kn5Error, Reader, decompose, node_matrix,  # noqa: F401
                           probe_kn5, quat_from_matrix, read_kn5, read_ksanim, read_node)
from kn5garage.textures import decode_dds_uncompressed, decode_image, image_for_gltf  # noqa: F401

if __name__ == "__main__":
    sys.exit(main())
