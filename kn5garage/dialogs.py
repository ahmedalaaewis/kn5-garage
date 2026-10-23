"""Native file / folder pickers (tkinter). They return None when cancelled or unavailable."""


def _tk():
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None, None
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root, filedialog


def pick_folder(title="Choose a car folder, or your assettocorsa\\content\\cars folder"):
    root, filedialog = _tk()
    if root is None:
        return None
    try:
        return filedialog.askdirectory(title=title) or None
    finally:
        root.destroy()


def pick_file(title, filetypes):
    root, filedialog = _tk()
    if root is None:
        return None
    try:
        return filedialog.askopenfilename(title=title, filetypes=filetypes) or None
    finally:
        root.destroy()


def pick_image():
    return pick_file("Choose a picture for the livery",
                     [("Pictures", "*.png *.jpg *.jpeg *.bmp *.tga *.tif *.tiff *.webp *.dds"), ("All files", "*")])


def pick_program():
    return pick_file("Choose a program (for example Photoshop.exe)", [("Programs", "*.exe"), ("All files", "*")])


def pick_model():
    return pick_file("Choose a 3D model", [("3D models", "*.glb *.gltf *.obj *.fbx *.kn5"), ("All files", "*")])


def pick_archive():
    return pick_file("Choose a downloaded mod or skin pack",
                     [("Archives", "*.zip *.7z *.rar"), ("All files", "*")])


def pick_blender_exe():
    root, filedialog = _tk()
    if root is None:
        return None
    try:
        return filedialog.askopenfilename(title="Find blender.exe",
                                          filetypes=[("Blender", "blender.exe blender"), ("All files", "*")]) or None
    finally:
        root.destroy()
