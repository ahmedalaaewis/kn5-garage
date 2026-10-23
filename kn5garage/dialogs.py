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


def pick_blender_exe():
    root, filedialog = _tk()
    if root is None:
        return None
    try:
        return filedialog.askopenfilename(title="Find blender.exe",
                                          filetypes=[("Blender", "blender.exe blender"), ("All files", "*")]) or None
    finally:
        root.destroy()
