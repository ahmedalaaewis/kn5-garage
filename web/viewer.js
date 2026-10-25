// A small three.js viewer that can live in any element. three.js is bundled in
// web/vendor/three (see index.html's import map) and loaded on first use.

let threeReady = null;

export function loadThree() {
  if (!threeReady) {
    threeReady = Promise.all([
      import('three'),
      import('three/addons/controls/OrbitControls.js'),
      import('three/addons/loaders/GLTFLoader.js'),
      import('three/addons/environments/RoomEnvironment.js'),
    ]).then(([THREE, oc, gl, env]) => ({
      THREE, OrbitControls: oc.OrbitControls, GLTFLoader: gl.GLTFLoader, RoomEnvironment: env.RoomEnvironment,
    }));
    threeReady.catch(() => { threeReady = null; });
  }
  return threeReady;
}

// Lighting setups: background, light strengths, floor colour, reflections on/off
// (night keeps soft reflections but a lower exposure, so the car stays visible and lights stand out)
export const STUDIOS = {
  studio: { bg: null, sun: 1.4, sunColor: 0xffffff, hemi: 0.6, floor: 0x1b2027, exposure: 1, grid: true },
  day: { bg: 0xa9c8e6, sun: 2.8, sunColor: 0xfff4e0, hemi: 1.1, floor: 0x6f7568, exposure: 1.05, grid: false },
  night: { bg: 0x070a12, sun: 0.9, sunColor: 0x8fa8ff, hemi: 0.25, floor: 0x10141c, exposure: 0.42, grid: false },
};

const STEER_RATIO = 12;  // steering wheel turns this much more than the road wheels

export class Viewer3D {
  constructor(stage, { spinBox = null, onInfo = null, onHover = null, onPick = null, onMeasure = null } = {}) {
    this.stage = stage;
    this.spinBox = spinBox;
    this.onInfo = onInfo;
    this.onHover = onHover;      // onHover({name, material} | null, x, y)
    this.onPick = onPick;        // onPick({name, material}) on a click (not a drag) on the car
    this.onMeasure = onMeasure;  // onMeasure(metres | null, points placed)
    this.mode = 'tex';
    this.studio = 'studio';
    this.model = null;
    this.clips = [];
    this.mixer = null;
    this.paintNames = null;  // material names chosen as paint; null = what the converter marked
    this.painted = new Set();
    this.lightsOn = false;
    this.steer = { wheels: [], columns: [], angle: 0 };
    this.bones = [];
    this.bonesShown = false;
    this.skeletonHelper = null;
    this.measuring = false;
    this.measurePoints = [];
    this.raf = 0;
    this.running = false;
    this.loadId = 0;
  }

  message(text) {
    let el = this.stage.querySelector('.loading');
    if (!text) { el?.remove(); return; }
    if (!el) { el = document.createElement('div'); el.className = 'loading'; this.stage.append(el); }
    el.textContent = text;
  }

  async init() {
    if (this.renderer) return true;
    let T;
    try { T = await loadThree(); }
    catch (e) { this.message('The 3D viewer could not start (three.js failed to load).'); return false; }
    const { THREE } = T;
    this.T = T;
    const r = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer = r;
    this.scene = new THREE.Scene();
    const pm = new THREE.PMREMGenerator(r);
    this.envMap = pm.fromScene(new T.RoomEnvironment(), 0.04).texture;
    pm.dispose();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 500);
    this.controls = new T.OrbitControls(this.camera, r.domElement);
    this.controls.enableDamping = true;
    this.controls.autoRotateSpeed = 1.2;
    this.clock = new THREE.Clock();
    this.sun = new THREE.DirectionalLight(0xffffff, 1.4);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0005;
    this.hemi = new THREE.HemisphereLight(0xdde6ff, 0x1a1410, 0.6);
    this.grid = new THREE.GridHelper(20, 40, 0x3a4450, 0x1f262e);
    this.floor = new THREE.Mesh(new THREE.CircleGeometry(12, 64), new THREE.MeshStandardMaterial({ color: 0x1b2027, roughness: 0.85 }));
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.position.y = -0.002;
    this.floor.receiveShadow = true;
    this.scene.add(this.sun, this.sun.target, this.hemi, this.grid, this.floor);
    this.clay = new THREE.MeshStandardMaterial({ color: 0xc9ced4, roughness: 0.55, metalness: 0.05, flatShading: true });
    this.wire = new THREE.MeshBasicMaterial({ color: 0xff6a45, wireframe: true });
    this.wireOver = new THREE.LineBasicMaterial({ color: 0xff6a45, transparent: true, opacity: 0.35 });
    this.highlight = new THREE.BoxHelper(undefined, 0xffd24d);
    this.highlight.visible = false;
    this.selection = new THREE.BoxHelper(undefined, 0x4dd2ff);
    this.selection.visible = false;
    this.selected = null;
    this.scene.add(this.highlight, this.selection);
    this.raycaster = new THREE.Raycaster();
    this.measureGroup = new THREE.Group();
    this.scene.add(this.measureGroup);
    this.setStudio(this.studio);
    this.stage.prepend(r.domElement);
    this.bindPointer(r.domElement);
    new ResizeObserver(() => this.resize()).observe(this.stage);
    this.resize();
    return true;
  }

  resize() {
    const w = this.stage.clientWidth, h = this.stage.clientHeight;
    if (!this.renderer || !w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // source: a URL or an ArrayBuffer holding a .glb
  async load(source, { keepView = false } = {}) {
    if (!(await this.init())) return;
    const id = ++this.loadId;  // a newer load() wins if two overlap
    this.start();
    if (!this.model) this.message('Loading model…');
    const loader = new this.T.GLTFLoader();
    let gltf;
    try {
      gltf = await (typeof source === 'string'
        ? loader.loadAsync(source)
        : new Promise((res, rej) => loader.parse(source, '', res, rej)));
    } catch (err) {
      if (id === this.loadId) this.message('Could not load the model: ' + (err.message || err));
      return;
    }
    if (id !== this.loadId) { disposeModel(gltf.scene); return; }
    this.message(null);
    const { THREE } = this.T;
    const model = gltf.scene;
    let tris = 0, meshes = 0;
    // glTF node index of each object: the same as the part's index in the kn5 (see parts.py)
    this.nodes = new Map();
    model.traverse((o) => {
      const a = gltf.parser.associations.get(o);
      if (a && a.nodes !== undefined && !this.nodes.has(a.nodes)) { this.nodes.set(a.nodes, o); o.userData.node = a.nodes; }
    });
    model.traverse((o) => {
      if (!o.isMesh) return;
      meshes++;
      tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
      o.userData.orig = o.material;
      o.castShadow = true;
      o.receiveShadow = true;
    });
    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    model.position.y -= box.min.y;
    const hadModel = !!this.model;
    this.stopAnimations();
    this.clearMeasure();
    this.highlight.visible = false;
    this.markedBone = null;
    if (this.boneMarker) this.boneMarker.visible = false;
    this.select(null);
    if (this.model) { this.scene.remove(this.model); disposeModel(this.model); }
    this.scene.add(model);
    this.model = model;
    this.clips = gltf.animations || [];
    this.mixer = this.clips.length ? new THREE.AnimationMixer(model) : null;
    this.painted = new Set();
    this.size = size;
    this.center = box.getCenter(new THREE.Vector3()).setY(size.y / 2);
    this.findSteering();
    this.findBones();
    this.fitShadow();
    this.setMode(this.mode);
    this.setLights(this.lightsOn);
    if (!(keepView && hadModel)) this.reset();
    this.onInfo?.({
      meshes, tris: Math.round(tris), size, animations: this.clips.map((c) => c.name),
      canSteer: this.steer.wheels.length > 0, lights: this.lightMaterials().length, bones: this.bones.length,
    });
  }

  reset() {
    if (!this.model) return;
    const s = this.size.length(), c = this.center;
    this.camera.position.set(c.x + s * 0.75, c.y + s * 0.35, c.z + s * 0.95);
    this.camera.near = s / 200; this.camera.far = s * 50; this.camera.updateProjectionMatrix();
    this.controls.target.copy(c);
    this.controls.update();
  }

  // ---- display modes and studios

  setMode(mode) {  // 'tex' | 'clay' | 'wire' | 'texwire'
    this.mode = mode;
    this.model?.traverse((o) => {
      if (!o.isMesh) return;
      o.material = mode === 'clay' ? this.clay : mode === 'wire' ? this.wire : o.userData.orig;
      if (mode === 'texwire' && !o.userData.wireOver) {
        const lines = new this.T.THREE.LineSegments(new this.T.THREE.WireframeGeometry(o.geometry), this.wireOver);
        lines.raycast = () => {};
        o.add(lines);
        o.userData.wireOver = lines;
      }
      if (o.userData.wireOver) o.userData.wireOver.visible = mode === 'texwire';
    });
  }

  setStudio(name) {
    const s = STUDIOS[name] || STUDIOS.studio;
    this.studio = name in STUDIOS ? name : 'studio';
    const { THREE } = this.T;
    this.scene.background = s.bg == null ? null : new THREE.Color(s.bg);
    this.scene.environment = this.envMap;
    this.renderer.toneMappingExposure = s.exposure;
    this.sun.color.set(s.sunColor);
    this.sun.intensity = s.sun;
    this.hemi.intensity = s.hemi;
    this.floor.material.color.set(s.floor);
    this.grid.visible = s.grid;
  }

  // The sun's shadow covers the car, whatever its size
  fitShadow() {
    const s = Math.max(this.size.x, this.size.z, 1);
    const cam = this.sun.shadow.camera;
    cam.left = cam.bottom = -s * 1.2;
    cam.right = cam.top = s * 1.2;
    cam.near = 0.1; cam.far = s * 12;
    cam.updateProjectionMatrix();
    this.sun.position.set(this.center.x + s * 1.5, s * 3, this.center.z + s * 1.2);
    this.sun.target.position.copy(this.center);
    this.floor.scale.setScalar(Math.max(1, s / 4));
  }

  // ---- materials and paint

  // Every material of the model once: [{name, paint, kind}] (marked by the converter)
  materials() {
    const seen = new Map();
    this.eachMaterial((m) => {
      if (!seen.has(m.name)) seen.set(m.name, { name: m.name, paint: !!m.userData?.kn5_paint, kind: m.userData?.kn5_kind || null });
    });
    return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  eachMaterial(fn) {
    const done = new Set();
    this.model?.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of [].concat(o.userData.orig || o.material)) if (m && !done.has(m)) { done.add(m); fn(m); }
    });
  }

  // names = list of material names to treat as paint, or null for the converter's guess
  setPaintNames(names) {
    this.paintNames = names && names.length ? new Set(names) : null;
  }

  paintMaterials() {
    const out = [];
    this.eachMaterial((m) => {
      if (this.paintNames ? this.paintNames.has(m.name) : m.userData?.kn5_paint) out.push(m);
    });
    return out;
  }

  // hex = '#rrggbb' or null to undo; mode 'tint' keeps the livery, 'solid' replaces it
  setPaint(hex, mode = 'tint') {
    for (const m of this.painted) restore(m);  // the targets may have changed since last time
    this.painted = new Set();
    const mats = this.paintMaterials();
    for (const m of mats) {
      m.userData.origColor ||= m.color.clone();
      if (!('origMap' in m.userData)) m.userData.origMap = m.map;
      if (!hex) { restore(m); continue; }
      m.color.set(hex);
      m.map = mode === 'solid' ? null : m.userData.origMap;
      m.needsUpdate = true;
      this.painted.add(m);
    }
    return mats.length;
  }

  // Swap a texture (by its file name in the model, e.g. "body.dds") for a new picture: the livery
  // studio calls this whenever the edited file is saved. Resolves to the number of materials changed.
  replaceTexture(name, url) {
    if (!this.model) return Promise.resolve(0);
    const { THREE } = this.T;
    const key = String(name).toLowerCase();
    return new THREE.TextureLoader().loadAsync(url).then((tex) => {
      tex.flipY = false;  // glTF texture layout
      tex.colorSpace = THREE.SRGBColorSpace;
      let n = 0;
      this.eachMaterial((m) => {
        const current = 'origMap' in m.userData ? m.userData.origMap : m.map;
        const match = current && (current.userData?.kn5Name || current.name || '').toLowerCase() === key;
        if (!match) return;
        tex.wrapS = current.wrapS;
        tex.wrapT = current.wrapT;
        tex.userData.kn5Name = current.userData?.kn5Name || current.name;
        if ('origMap' in m.userData) m.userData.origMap = tex;
        if (m.map === current) m.map = tex;
        m.needsUpdate = true;
        n++;
      });
      return n;
    });
  }

  // ---- lights (materials the converter marked as head / tail lights)

  lightMaterials() {
    const out = [];
    this.eachMaterial((m) => { if (m.userData?.kn5_kind === 'light') out.push(m); });
    return out;
  }

  setLights(on) {
    this.lightsOn = on;
    for (const m of this.lightMaterials()) {
      if (!('origEmissive' in m.userData)) {
        m.userData.origEmissive = m.emissive.clone();
        m.userData.origEmissiveMap = m.emissiveMap;
        m.userData.origEmissiveIntensity = m.emissiveIntensity;
      }
      if (on) {
        m.emissive.set(0xffffff);
        m.emissiveMap = m.map || null;
        m.emissiveIntensity = 6;
      } else {
        m.emissive.copy(m.userData.origEmissive);
        m.emissiveMap = m.userData.origEmissiveMap;
        m.emissiveIntensity = m.userData.origEmissiveIntensity;
      }
      m.needsUpdate = true;
    }
  }

  // ---- steering (front wheels WHEEL_LF / WHEEL_RF, steering wheel STEER_HR / STEER_LR)

  findSteering() {
    this.steer = { wheels: [], columns: [], angle: 0 };
    this.model.traverse((o) => {
      if (/^WHEEL_[LR]F$/i.test(o.name)) this.steer.wheels.push({ node: o, base: o.quaternion.clone() });
      if (/^STEER_(HR|LR)$/i.test(o.name)) this.steer.columns.push({ node: o, base: o.quaternion.clone() });
    });
  }

  // degrees of road-wheel angle, + = left
  setSteer(deg) {
    if (!this.T) return;
    const { THREE } = this.T;
    this.steer.angle = deg;
    const a = THREE.MathUtils.degToRad(deg);
    const up = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), a);
    for (const w of this.steer.wheels) w.node.quaternion.copy(w.base).multiply(up);
    const spin = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), -a * STEER_RATIO);
    for (const c of this.steer.columns) c.node.quaternion.copy(c.base).multiply(spin);
  }

  // ---- animations (doors, wipers, lights… from the car's .ksanim files)

  animations() { return this.clips.map((c) => c.name); }

  // Plays one clip back and forth; returns false if there is no such clip
  playAnimation(name) {
    const clip = this.clips.find((c) => c.name === name);
    if (!clip || !this.mixer) return false;
    this.mixer.stopAllAction();
    const action = this.mixer.clipAction(clip);
    // characters walk in loops; car doors and wipers go back and forth
    action.setLoop(this.bones.length ? this.T.THREE.LoopRepeat : this.T.THREE.LoopPingPong, Infinity);
    action.reset().play();
    return true;
  }

  stopAnimations() {
    if (!this.mixer) return;
    this.mixer.stopAllAction();
    this.mixer.setTime(0);
  }

  // ---- pointer: hover names, picking parts, measuring

  bindPointer(el) {
    let down = null;
    let pending = null;
    el.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    el.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;  // a drag, not a click
      const hit = this.hitTest(e);
      if (this.measuring) this.addMeasurePoint(hit);
      else if (hit) this.onPick?.(describe(hit.object));
    });
    el.addEventListener('pointermove', (e) => {
      if (e.buttons) return;
      pending = e;
      requestAnimationFrame(() => {
        if (pending !== e) return;
        const hit = this.hitTest(e);
        if (hit) {
          this.highlight.setFromObject(hit.object);
          this.highlight.visible = true;
        } else this.highlight.visible = false;
        const rect = el.getBoundingClientRect();
        this.onHover?.(hit ? describe(hit.object) : null, e.clientX - rect.left, e.clientY - rect.top);
      });
    });
    el.addEventListener('pointerleave', () => { this.highlight.visible = false; this.onHover?.(null); });
  }

  hitTest(e) {
    if (!this.model) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const p = new this.T.THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(p, this.camera);
    return this.raycaster.intersectObject(this.model, true).find((h) => h.object.isMesh && h.object.visible) || null;
  }

  setMeasuring(on) {
    this.measuring = on;
    this.clearMeasure();
  }

  clearMeasure() {
    this.measurePoints = [];
    this.measureGroup?.clear();
  }

  addMeasurePoint(hit) {
    if (!hit) return;
    const { THREE } = this.T;
    if (this.measurePoints.length >= 2) this.clearMeasure();
    this.measurePoints.push(hit.point.clone());
    const r = Math.max(this.size.length() / 300, 0.004);
    const dot = new THREE.Mesh(new THREE.SphereGeometry(r, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffd24d, depthTest: false }));
    dot.renderOrder = 10;
    dot.position.copy(hit.point);
    this.measureGroup.add(dot);
    if (this.measurePoints.length === 2) {
      const [a, b] = this.measurePoints;
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]),
        new THREE.LineBasicMaterial({ color: 0xffd24d, depthTest: false }));
      line.renderOrder = 10;
      this.measureGroup.add(line);
      this.onMeasure?.(a.distanceTo(b), 2);
    } else this.onMeasure?.(null, 1);
  }

  // ---- characters: bones, showing the skeleton, posing

  // Bones: the joints of rigged meshes; for a skeleton without a mesh (BVH), the animated nodes
  findBones() {
    const set = new Set();
    this.model.traverse((o) => { if (o.isSkinnedMesh) o.skeleton.bones.forEach((b) => set.add(b)); });
    if (!set.size) {
      const animated = new Set(this.clips.flatMap((c) => c.tracks.map((tr) => tr.name.split('.')[0])));
      this.model.traverse((o) => { if (!o.isMesh && animated.has(o.name)) set.add(o); });
    }
    this.bones = [...set];
    for (const b of this.bones) b.userData.rest = b.quaternion.clone();
    this.skeletonHelper?.removeFromParent();
    this.skeletonHelper = null;
    if (this.bonesShown) this.showBones(true);
  }

  // A line from every bone to its parent bone, drawn on top of the model (updated each frame)
  showBones(on) {
    this.bonesShown = on;
    this.skeletonHelper?.removeFromParent();
    this.skeletonHelper?.geometry.dispose();
    this.skeletonHelper = null;
    if (!on || !this.bones.length) return;
    const { THREE } = this.T;
    const set = new Set(this.bones);
    this.bonePairs = this.bones.filter((b) => set.has(b.parent)).map((b) => [b, b.parent]);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(Math.max(this.bonePairs.length, 1) * 6), 3));
    const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x4dd2ff, depthTest: false, transparent: true }));
    lines.renderOrder = 20;
    lines.frustumCulled = false;
    this.skeletonHelper = lines;
    this.scene.add(lines);
    this.updateBoneLines();
  }

  updateBoneLines() {
    if (this.markedBone && this.boneMarker) this.markedBone.getWorldPosition(this.boneMarker.position);
    const lines = this.skeletonHelper;
    if (!lines) return;
    const pos = lines.geometry.attributes.position;
    const v = new this.T.THREE.Vector3();
    this.bonePairs.forEach(([b, p], i) => {
      b.getWorldPosition(v); pos.setXYZ(i * 2, v.x, v.y, v.z);
      p.getWorldPosition(v); pos.setXYZ(i * 2 + 1, v.x, v.y, v.z);
    });
    pos.needsUpdate = true;
  }

  // degrees about the bone's own X / Y / Z, on top of its rest pose
  poseBone(i, deg) {
    const b = this.bones[i];
    if (!b) return;
    const { THREE } = this.T;
    this.stopAnimations();
    const r = deg.map((d) => THREE.MathUtils.degToRad(d));
    b.quaternion.copy(b.userData.rest).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(r[0], r[1], r[2])));
    b.updateMatrixWorld(true);
  }

  // a small dot on the chosen bone (kept on it while it moves)
  markBone(i) {
    const { THREE } = this.T;
    this.markedBone = i == null ? null : this.bones[i];
    if (!this.boneMarker) {
      this.boneMarker = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12),
        new THREE.MeshBasicMaterial({ color: 0xffd24d, depthTest: false, transparent: true, opacity: 0.9 }));
      this.boneMarker.renderOrder = 21;
      this.scene.add(this.boneMarker);
    }
    this.boneMarker.visible = !!this.markedBone;
    this.boneMarker.scale.setScalar(Math.max((this.size?.length() || 1) / 150, 0.004));
    this.updateBoneLines();
  }

  resetPose() {
    for (const b of this.bones) b.quaternion.copy(b.userData.rest);
    this.model?.updateMatrixWorld(true);
  }

  // ---- parts (by glTF node index)

  nodeObject(i) { return this.nodes?.get(i) || null; }

  select(i) {
    this.selected = i == null ? null : this.nodeObject(i);
    if (this.selected) this.selection.setFromObject(this.selected);
    this.selection.visible = !!this.selected && this.selected.visible;
  }

  setNodeVisible(i, on) {
    const o = this.nodeObject(i);
    if (o) o.visible = on;
    this.select(this.selected?.userData.node ?? null);
  }

  // matrix: 16 numbers in glTF order, the part's local transform
  setNodeMatrix(i, matrix) {
    const o = this.nodeObject(i);
    if (!o) return;
    o.matrix.fromArray(matrix);
    o.matrix.decompose(o.position, o.quaternion, o.scale);
    o.updateMatrixWorld(true);
    if (this.selected === o) this.selection.setFromObject(o);
  }

  // ---- pictures and video

  // One picture from the current view, at a fixed size; resolves to a PNG data URL
  // background: a colour for pictures that can't be transparent (JPEG skin previews)
  snapshot({ width = 1920, height = 1080, background = null } = {}) {
    if (!this.model) return null;
    const keep = this.scene.background;
    if (background != null && !keep) this.scene.background = new this.T.THREE.Color(background);
    try {
      return this.withSize(width, height, () => {
        this.renderer.render(this.scene, this.camera);
        return this.renderer.domElement.toDataURL('image/png');
      });
    } finally { this.scene.background = keep; }
  }

  withSize(width, height, fn) {
    const { THREE } = this.T;
    const r = this.renderer, cam = this.camera;
    const keep = { aspect: cam.aspect, size: r.getSize(new THREE.Vector2()), ratio: r.getPixelRatio(), grid: this.grid.visible,
      sel: this.selection.visible };
    r.setPixelRatio(1);
    r.setSize(width, height, false);
    cam.aspect = width / height;
    cam.updateProjectionMatrix();
    this.grid.visible = false;
    this.highlight.visible = false;
    this.selection.visible = false;
    try { return fn(); }
    finally {
      this.grid.visible = keep.grid;
      this.selection.visible = keep.sel;
      r.setPixelRatio(keep.ratio);
      r.setSize(keep.size.x, keep.size.y);
      cam.aspect = keep.aspect;
      cam.updateProjectionMatrix();
    }
  }

  // Render `frames` pictures going once around the car; onFrame(index, dataUrl) may be async.
  async turntable({ frames = 36, width = 1600, height = 900, onFrame }) {
    if (!this.model) return;
    const cam = this.camera, ctl = this.controls;
    const keepPos = cam.position.clone();
    const target = ctl.target.clone();
    const off = keepPos.clone().sub(target);
    const radius = Math.hypot(off.x, off.z), y = off.y, start = Math.atan2(off.x, off.z);
    this.stop();
    try {
      for (let i = 0; i < frames; i++) {
        const a = start + (i / frames) * Math.PI * 2;
        cam.position.set(target.x + Math.sin(a) * radius, target.y + y, target.z + Math.cos(a) * radius);
        cam.lookAt(target);
        const data = this.withSize(width, height, () => {
          this.renderer.render(this.scene, cam);
          return this.renderer.domElement.toDataURL('image/png');
        });
        await onFrame(i, data);
      }
    } finally {
      cam.position.copy(keepPos);
      ctl.update();
      this.start();
    }
  }

  // Record a spinning video of the car; resolves to a WebM Blob.
  recordVideo(seconds = 8) {
    if (!this.model || !window.MediaRecorder) return Promise.reject(new Error('Video recording is not supported in this browser'));
    const r = this.renderer, cam = this.camera, target = this.controls.target.clone();
    const off = cam.position.clone().sub(target);
    const radius = Math.hypot(off.x, off.z), y = off.y, start = Math.atan2(off.x, off.z);
    const keepPos = cam.position.clone();
    const type = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t));
    if (!type) return Promise.reject(new Error('This browser cannot record WebM video'));
    const rec = new MediaRecorder(r.domElement.captureStream(30), { mimeType: type, videoBitsPerSecond: 12e6 });
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    this.stop();
    const keepBg = this.scene.background;
    if (!keepBg) this.scene.background = new this.T.THREE.Color(0x14181d);
    const keepGrid = this.grid.visible;
    this.grid.visible = false;
    this.highlight.visible = false;
    return new Promise((resolve) => {
      rec.onstop = () => {
        this.scene.background = keepBg;
        this.grid.visible = keepGrid;
        cam.position.copy(keepPos);
        this.controls.update();
        this.start();
        resolve(new Blob(chunks, { type: 'video/webm' }));
      };
      rec.start();
      const t0 = performance.now();
      const step = () => {
        const t = (performance.now() - t0) / 1000 / seconds;
        if (t >= 1) { rec.stop(); return; }
        const a = start + t * Math.PI * 2;
        cam.position.set(target.x + Math.sin(a) * radius, target.y + y, target.z + Math.cos(a) * radius);
        cam.lookAt(target);
        this.mixer?.update(this.clock.getDelta());
        r.render(this.scene, cam);
        requestAnimationFrame(step);
      };
      step();
    });
  }

  // ---- render loop (paused while the viewer is hidden)

  start() {
    if (this.running || !this.renderer) return;
    this.running = true;
    this.clock.getDelta();
    const loop = () => {
      if (!this.running) return;
      this.raf = requestAnimationFrame(loop);
      this.controls.autoRotate = !!this.spinBox?.checked && !this.measuring;
      this.controls.update();
      this.mixer?.update(this.clock.getDelta());
      if (this.bonesShown || this.markedBone) this.updateBoneLines();
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }
}

function describe(mesh) {
  const m = [].concat(mesh.userData.orig || mesh.material)[0];
  return { name: mesh.name || '(unnamed)', material: m?.name || '', paint: !!m?.userData?.kn5_paint, kind: m?.userData?.kn5_kind || null,
    node: mesh.userData.node ?? null };
}

function restore(m) {
  if (m.userData.origColor) m.color.copy(m.userData.origColor);
  if ('origMap' in m.userData) m.map = m.userData.origMap;
  m.needsUpdate = true;
}

function disposeModel(model) {
  model.traverse((o) => {
    if (o.userData?.wireOver) o.userData.wireOver.geometry.dispose();
    if (!o.isMesh) return;
    o.geometry.dispose();
    const m = o.userData.orig || o.material;
    for (const mat of Array.isArray(m) ? m : [m]) {
      if (!mat) continue;
      for (const v of Object.values(mat)) if (v && v.isTexture) v.dispose();
      mat.dispose();
    }
  });
}
