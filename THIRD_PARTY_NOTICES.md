# Third-party software

## three.js 0.160.0 (bundled)

`web/vendor/three/` contains unmodified files from [three.js](https://threejs.org)
release 0.160.0 (`build/three.module.js` and the add-ons `OrbitControls`, `GLTFLoader`,
`RoomEnvironment` and `BufferGeometryUtils`), downloaded from the npm registry
(integrity `sha512-DLU8lc0zNIPkM7rH5/e1Ks1Z8tWCGRq6g8mPowdDJpw1CFBJMU7UoJjC6PefXW7z//SSl0b2+GCw14LB+uDhng==`).
It is MIT licensed; the licence is in `web/vendor/three/LICENSE`.

To update it, replace those files with the same files from a newer release and run the tests.

## Python packages

Installed separately when running from source, and bundled inside `KN5Garage.exe`:

- [NumPy](https://numpy.org): BSD-3-Clause licence.
- [Pillow](https://python-pillow.org): MIT-CMU (HPND) licence.
- Python itself: PSF licence.

`KN5Garage.exe` is built with [PyInstaller](https://pyinstaller.org), whose bootloader is GPL-2.0
with an exception that allows distributing programs built with it under any licence.

## Trademarks

Assetto Corsa is a trademark of Kunos Simulazioni / 505 Games. KN5 Garage is an independent
tool and is not affiliated with or endorsed by them. Blender is a trademark of the Blender
Foundation.
