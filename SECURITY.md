# Security

KN5 Garage runs a small web server on your own PC and reads car mods downloaded from
anywhere, so it is built to be safe against hostile web pages and hostile mod files.

## How the app is protected

| Threat | Protection |
|---|---|
| Other computers on your network | The server listens on `127.0.0.1` only. |
| A website you visit calling the app (CSRF) | Every API call needs a random token created at each start, known only to the app's own page and sent in an `X-Token` header. Cross-site requests (`Origin` / `Sec-Fetch-Site`) and non-JSON posts are refused. |
| DNS rebinding (a website pretending to be `127.0.0.1`) | Requests must name `127.0.0.1:<port>` or `localhost:<port>` in the `Host` header, so a rebinding page can't load the app or read the token. |
| Clickjacking / framing | `X-Frame-Options: DENY` and `frame-ancestors 'none'`. |
| Script injection from mod text (names, authors, descriptions) | Mod text is only inserted as text, never as HTML (`web/js/dom.js`); HTML tags are also stripped on the server. A strict Content-Security-Policy allows only the app's own scripts. Author links are only clickable when they are plain `http(s)` links. |
| Spreadsheet formula injection in the CSV export | Cells starting with `= + - @` are prefixed with `'`. |
| Reading files outside the car / output folders | File routes only serve images inside a scanned car folder and `.glb` files directly inside the output folder; `..`, absolute paths, UNC paths and encoded tricks are rejected. A car's `lods.ini` can't point outside its folder. |
| Hostile or damaged `.kn5` / `.ksanim` / image files | Every count and length is checked against the real file size, node nesting is limited, images over 16K×16K are refused, and fuzz tests check that broken files only produce a clean error. |
| Running other programs | Blender (an executable named `blender` / `blender.exe`), 7-Zip and image programs are started with a fixed argument list and no shell. |
| Leaking your data | The page loads nothing from the internet (three.js is bundled, no web fonts, no analytics). Nothing is logged. Blender scripts contain no absolute paths. Paths on screen show `~` instead of your user name. Favourites, tags and history stay in local JSON files that git ignores. |
| Converting to FBX | Blender runs in the background with `--factory-startup` (no add-ons or startup scripts) and a fixed script passed as an argument, so no temporary script file can be swapped. |
| Huge requests | JSON bodies are limited to 1 MB (130 MB for turntable frames and videos). |
| Hostile mod archives (`.zip`, `.7z`, `.rar`) | The file list is checked before anything is unpacked: absolute paths, `..`, drive letters, alternate data streams and links are refused, and there are caps on the number of files, their total size and the packed-to-unpacked ratio (zip bombs). Programs and scripts (`.exe`, `.dll`, `.bat`, `.ps1`, …) are never unpacked. Archives go into the app's work folder and are checked again (no links or junctions) before car and skin folders are copied out. `.7z` / `.rar` are listed and unpacked by 7-Zip with a fixed argument list. |
| Hostile 3D files (`.glb`, `.gltf`, `.obj`) | Files a model refers to must sit next to it (no absolute paths or `..`); sizes, vertex counts and nesting are capped; damaged files only produce an error. |
| Changing car folders | Only on Save, after asking. Writes must stay inside the car folder; every changed, created or removed file is backed up first (or the edit goes to a new copy), and backups are restored only from inside the backups folder. Replaced mods are moved to the backups, never deleted. Packed `data.acd` files are never decrypted. |
| Image programs (Photoshop, GIMP…) | Only programs found in their usual install folders, or picked by you in a file dialog, are started, with one file from the app's work folder and no shell. |

Exports only read car folders and write to the output folder. Edits and installs write to car
folders only when you save or install, as described above.

## The Windows download

`KN5Garage.exe` is built from this source by the public release workflow on GitHub's servers
(`.github/workflows/release.yml`), tested from a clean folder, and published with a SHA-256
checksum. It is not code-signed, so Windows SmartScreen may warn the first time you run it;
you can check the checksum, or build it yourself with `build_exe.bat`.

## What it does not protect against

Any program already running on your PC as you can do what you can do; that includes
reaching a server on `127.0.0.1`. KN5 Garage doesn't try to defend against malware that is
already on the machine.

## Tests

`tests/test_security.py` attacks the running server (DNS rebinding, CSRF, framing,
path traversal, oversized and malformed requests, hostile mod text),
`tests/test_kn5_reader.py` fuzzes the model reader, and `tests/test_import.py`,
`tests/test_edits.py` and `tests/test_livery.py` try hostile archives, paths and backups. Run everything with `run_tests.bat`.

## Reporting a problem

If you find a security issue, please report it privately to the maintainers (for example with
GitHub's "Report a vulnerability" button on the repository) rather than in a public issue.
