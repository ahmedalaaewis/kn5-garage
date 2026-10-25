# Changelog

## 1.3.0

- **Not only Assetto Corsa:** BeamNG.drive vehicles (folders and zip mods: default configuration,
  materials and skins) and loose 3D files appear in the library next to the cars, with a filter by
  kind. Preview, export, favourites, tags and compare work for all of them.
- **12 formats without other programs:** native readers for binary FBX (rigs and animations
  included), COLLADA (.dae), STL, PLY, 3DS, MikuMikuDance PMX and BVH motion capture; glTF and VRM
  keep their rigs and animations. Clear messages for formats that can't be read (SketchUp .skb /
  .skp, .blend, Forza files…).
- **Characters:** the viewer shows the skeleton, lists the bones and poses them with sliders;
  walking animations loop.
- **3D printing:** real size in millimetres, repair, wall thickness for thin surfaces, cutting into
  closed pieces, STL and 3MF.
- **Textures:** convert DDS (compressed too), TGA, PSD and more to PNG, JPEG, TGA, DDS, WebP or BMP,
  resized or with normal maps flipped.
- Wording made game-neutral; the new screens are translated into all 12 other languages.

## 1.2.0

- **Edit cars** (new Edit tab): engine power, rev limiter, mass, fuel, final drive and turbo
  boost from an unpacked `data` folder, and the specs and power/torque charts in the game menus.
  Every save asks: a new car next to the original, or the original with a backup and **Undo**.
- **Livery studio:** paint textures open in Photoshop, Illustrator, GIMP, Krita, Paint.NET,
  Affinity, Inkscape (found automatically) or any program you add; the 3D view updates each time
  you save there. UV guide, use a picture, and install as a new skin with preview and icon.
- **Parts editor:** hide, remove, move / rotate / scale, recolour or change the material of any
  part, and swap in parts from other cars or 3D files; undo and redo; lossless `.kn5` writing.
- **Import:** install mods and skin packs from `.zip` (and `.7z` / `.rar` with 7-Zip) with
  skip / rename / replace-with-backup; open `.glb`, `.gltf`, `.obj`, `.fbx` and `.kn5` files to
  view or convert; make a new car from a 3D model on a donor car (beta).
- **Library:** cars are found at any depth below an added folder (folders inside folders).
- All new screens translated into the 12 other languages.

## 1.1.0

- **Download and run:** `KN5Garage.exe`, one file, no Python needed (built by `build_exe.bat`
  and published automatically for tagged releases). `KN5Garage convert …` is the command line.
- **Exports:** the driver is exported with its skeleton when its bones are in the model;
  head and tail lights can glow (KHR_materials_emissive_strength); glass gets real transmission,
  chrome and carbon proper materials; new **OBJ** and **FBX** (via Blender) presets; texture size
  and JPEG options; saved export presets; an export history with "Again".
- **Viewer:** steering slider (front wheels and steering wheel), lights on/off, studio / day /
  night lighting with shadows, a measuring tool, part names on hover, texture + wireframe view,
  pick paint parts by clicking the car.
- **Library:** favourites and your own tags, disk-space view (what identical copies waste),
  skin gallery across all cars, Custom Shaders Patch features, real specs from unpacked `data`
  folders, Content Manager variants.
- **13 languages:** English, Deutsch, Español, Français, Italiano, Polski, Português, Türkçe,
  Русский, العربية (with a right-to-left layout), 日本語, 한국어, 中文 (one file each in `web/js/lang/`).
- **Privacy:** paths on screen show `~` instead of your Windows user name.
- Deep links (`#car=…&open=compare`) for bookmarks; a demo garage and a screenshot tool.
- GitHub release workflow, issue and pull-request templates.

## 1.0.0

- Library: several car folders, cached scans, card and sortable table views, stats that
  filter, duplicate detection (same name, or identical model file under any name), issues,
  CSV export, compare 2–6 cars.
- Export presets: Blender render, game-ready (LOD + 1K textures), 3D printing (STL in mm);
  paint colour (tint or solid) with the paint parts chosen automatically or by hand;
  txMaps as metallic/roughness, clearcoat paint, part pivots and `.ksanim` animations;
  an export report; cancel running exports; textures converted in parallel.
- 3D viewer: quick previews with a skin strip, paint picker and paint parts, animation
  playback, snapshot, turntable and video; Open in Blender with a scene-setup script.
- Security: localhost-only server with a per-run token, Host / Origin checks against DNS
  rebinding and CSRF, strict Content-Security-Policy, request size limits, hardened file
  routes and model reader; nothing is loaded from the internet.
- Code split into the `kn5garage` package and `web/js` modules; unit, security, web and
  end-to-end tests; CI workflow.
