"""KN5 Garage: convert Assetto Corsa car models (.kn5) to glTF (.glb), OBJ, FBX and STL.

Modules:
    kn5        reading .kn5 models and .ksanim animations
    textures   decoding DDS / PNG / JPEG textures for glTF
    glb        writing binary glTF
    carfiles   finding models, LODs, skins and animations in a car folder
    convert    presets and the converter (glb, obj, stl; fbx through Blender)
    carinfo    car details for the UI (names, specs, skins, issues, CSP, data folder)
    blender    finding Blender, the scene-setup script, FBX conversion
    settings, userdata, jobs, cache, server   the local web app
    paths, dialogs, cli, __main__             running from source or as KN5Garage.exe
"""

__version__ = "1.3.0"
