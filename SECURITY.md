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
| Running other programs | Only an executable named `blender` / `blender.exe` is ever launched, with a fixed argument list (no shell). |
| Leaking your data | The page loads nothing from the internet (three.js is bundled, no web fonts, no analytics). Nothing is logged. Blender scripts contain no absolute paths. Paths on screen show `~` instead of your user name. Favourites, tags and history stay in local JSON files that git ignores. |
| Converting to FBX | Blender runs in the background with `--factory-startup` (no add-ons or startup scripts) and a fixed script passed as an argument, so no temporary script file can be swapped. |
| Huge requests | JSON bodies are limited to 1 MB (130 MB for turntable frames and videos). |

Your car folders are only ever read. Files are written only to the output folder.

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
path traversal, oversized and malformed requests, hostile mod text) and
`tests/test_kn5_reader.py` fuzzes the model reader. Run everything with `run_tests.bat`.

## Reporting a problem

If you find a security issue, please report it privately to the maintainers (for example with
GitHub's "Report a vulnerability" button on the repository) rather than in a public issue.
