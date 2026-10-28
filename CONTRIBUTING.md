# Contributing

Thanks for helping! A few things keep the project easy to work on.

## Setup

```
python -m pip install -r requirements.txt
python app.py            # the app
run_tests.bat            # all tests (or the commands below)
```

Node.js is optional; it runs the web logic tests in `tests/web`.

## Layout

| Path | What it is |
|---|---|
| `kn5garage/kn5.py` | Reading `.kn5` models and `.ksanim` animations |
| `kn5garage/textures.py`, `glb.py` | Textures for glTF, the `.glb` writer |
| `kn5garage/carfiles.py` | Finding models, LODs, skins and animations in a car folder |
| `kn5garage/convert.py` | Presets and the converter (`.glb` / `.stl`) |
| `kn5garage/carinfo.py` | Car details for the UI |
| `kn5garage/kn5write.py` | Writing `.kn5` models (byte-for-byte the same when nothing changed) |
| `kn5garage/edits.py`, `physics.py` | Saving edits (new car or original + backup, undo), physics and menu specs |
| `kn5garage/livery.py`, `editors.py` | The livery studio, finding and starting image programs |
| `kn5garage/parts.py`, `builder.py` | The parts editor (steps with undo / redo), making a car from a 3D model |
| `kn5garage/importer.py`, `installer.py` | Reading `.glb` / `.gltf` / `.vrm` / `.obj` and shared helpers, installing mods from archives |
| `kn5garage/formats/` | One `load(path)` for every 3D format: `fbx.py`, `collada.py`, `simple.py` (STL, PLY, 3DS), `characters.py` (PMX, BVH), `sketchup.py` (SKP / SKB) |
| `kn5garage/items.py`, `beamng.py` | What the library holds (AC cars, BeamNG vehicles, 3D files) and BeamNG vehicles |
| `kn5garage/printing.py`, `texturetool.py` | 3D printing (repair, thickness, cutting, STL / 3MF), texture conversion |
| `kn5garage/server.py` | The local web server and API |
| `kn5garage/jobs.py`, `cache.py`, `settings.py`, `userdata.py` | Export queue, scan / preview caches, settings, favourites / tags / history |
| `kn5garage/blender.py`, `dialogs.py`, `cli.py` | Blender (scene script, FBX), file pickers, command line |
| `kn5garage/paths.py`, `__main__.py` | Where files live from source and inside `KN5Garage.exe`; the entry point |
| `web/js/*.js` | The page, one module per part (library, detail, compare, exporter, extras, viewer-ui, editor, livery, parts, importer…) |
| `web/js/model.js` | Pure front-end logic (sorting, filtering, CSV, paths), tested with Node |
| `web/js/i18n.js` | Translations (English text is the key) |
| `web/viewer.js` | The three.js viewer (studios, steering, lights, measuring, hover) |
| `tests/` | Unit, security, web and end-to-end tests, plus fixtures that build fake cars |
| `tools/` | The demo garage and the README screenshot tool |
| `packaging/`, `build_exe.bat` | Building and checking `KN5Garage.exe` |

`app.py` and `kn5_to_glb.py` are small entry points kept for the launchers.

## Translations

Every piece of page text is English in the code; each language is one file in `web/js/lang/`
mapping that English text to a translation. Static text in `index.html` is marked with `data-i18n`
(or `data-i18n-title` / `data-i18n-placeholder`), scripts use `t('English text', {vars})`.
To add a language, copy a file in `web/js/lang/`, translate the values (keep `{placeholders}`),
import it in `web/js/i18n.js` and add it to `LANGUAGES` (and to `RTL` for right-to-left scripts).
The tests check that every language has all strings with the same placeholders.
Corrections from native speakers are very welcome: the translations were not made by native speakers.

## Releases

Update the version in `kn5garage/__init__.py`, `pyproject.toml` and `CHANGELOG.md`, run
`python tools/screenshots.py` if the page changed, then push a tag like `v1.2.0`: the release
workflow builds `KN5Garage.exe`, tests it, and publishes the zip with a SHA-256 checksum.

## Rules

- **Car folders are read-only.** Never write inside a car folder; write only to the output folder.
- **Treat mod files as hostile.** Check every count and length read from a file; mod text goes
  into the page only as text (`h()` / `s()` in `web/js/dom.js`), never through `innerHTML`.
- **No outside requests from the page.** Bundle what you need under `web/vendor/` and list it
  in `THIRD_PARTY_NOTICES.md`; the Content-Security-Policy blocks everything else.
- **No personal data in the repository.** `settings.json` and `output/` are ignored by git;
  don't add absolute paths, user names or real car files to code, tests or docs.
- **Add a test** for every fix and feature. Tests build their own fake cars (`tests/fixtures.py`).
- Match the style around you: small functions, plain names, comments that say *why*.

## Before a pull request

Run `run_tests.bat` (or `python -m unittest discover -s tests`, `node --test "tests/web/*.test.mjs"`
and `python tests/smoke_test.py`) and make sure everything passes.
