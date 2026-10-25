// The full-screen 3D viewer: exported files and quick previews, skins, paint and paint parts,
// studios, steering, lights, measuring, hover names, animations, pictures and videos.

import { Viewer3D } from '../viewer.js';
import { api, fileUrl, outUrl, previewUrl, q } from './api.js';
import { openInBlender } from './blender.js';
import { $, $$, h, img, segValue, wireSeg } from './dom.js';
import { t } from './i18n.js';
import { displayPath } from './model.js';
import { emit, prefs, state } from './state.js';
import { flash } from './toast.js';

const view = { car: null, file: null, name: '' };  // what the viewer is showing
let paintOn = false;

export const viewer = new Viewer3D($('#stage'), {
  spinBox: $('#spinChk'),
  onInfo: (info) => {
    emit('viewer-info', info);
    infoChips(info);
    applyPaint();
    renderAnimations(info.animations);
    renderParts();
    $('#steerTool').hidden = !info.canSteer;
    $('#steerRange').value = 0;
    $('#lightsBtn').hidden = !info.lights;
  },
  onHover: (part, x, y) => {
    const tip = $('#hoverTip');
    tip.hidden = !part;
    if (!part) return;
    tip.replaceChildren(...[h('b', {}, part.name), h('span', {}, part.material),
      part.paint && h('span', { class: 'flag-pill info' }, t('Paint')),
      part.kind && h('span', { class: 'flag-pill info' }, part.kind)].filter(Boolean));
    tip.style.transform = `translate(${Math.round(x + 14)}px, ${Math.round(y + 14)}px)`;
  },
  onPick: (part) => {
    if (!$('#partsPanel').hidden) return emit('viewer-pick', part);  // the parts editor is choosing
    if ($('#partsList').hidden || !part.material) return;  // picking parts only while the list is open
    const chosen = new Set(currentParts());
    chosen.has(part.material) ? chosen.delete(part.material) : chosen.add(part.material);
    setParts([...chosen]);
    renderParts();
  },
  onMeasure: (metres, points) => {
    $('#measureInfo').textContent = metres == null
      ? (points ? t('Click a second point') : t('Click two points on the car'))
      : `${metres.toFixed(3)} m · ${Math.round(metres * 1000)} mm`;
  },
});

function infoChips(info) {
  $('#viewerInfo').replaceChildren(
    h('span', { class: 'chip strong' }, t('{n} parts', { n: info.meshes })),
    h('span', { class: 'chip strong' }, t('{n} triangles', { n: info.tris.toLocaleString() })),
    h('span', { class: 'chip strong' }, `${info.size.x.toFixed(2)} × ${info.size.y.toFixed(2)} × ${info.size.z.toFixed(2)} m`),
    h('span', { class: 'chip' }, t('Drag to orbit · scroll to zoom · right-drag to pan')));
}

function show(title) {
  $('#viewer').hidden = false;
  $('#viewerTitle').textContent = title;
  $('#viewerInfo').replaceChildren();
  $('#paintInfo').textContent = '';
  $('#animSel').hidden = true;
  $('#partsList').hidden = true;
  $('#hoverTip').hidden = true;
  paintOn = false;  // each car starts with its own colours
  setMeasure(false);
  viewer.setPaintNames(view.car ? state.paintNames[view.car] : null);
}

// The car an exported file came from, if it was exported in this session
function carForFile(file) {
  return Object.keys(state.done).find((id) => state.done[id].file === file) || null;
}

// An exported .glb from the output folder
export function openViewer(file, title, carId = null) {
  view.car = carId || carForFile(file);
  view.file = file;
  view.name = file.replace(/\.glb$/i, '');
  show(title);
  $('#viewerSkins').replaceChildren();
  $('#viewerBlender').hidden = false;
  return viewer.load(outUrl(file));
}

// A light preview of a scanned car, made on demand by the server
export async function openPreview(id) {
  view.car = id;
  view.file = null;
  $('#viewerBlender').hidden = true;
  if (!state.details[id]) {
    try { state.details[id] = await api(`/api/car?${q({ id })}`); } catch (e) { /* the strip just stays empty */ }
  }
  show(state.details[id]?.name || id);
  renderSkins();
  await loadPreview(false);
}

async function loadPreview(keepView) {
  const id = view.car;
  const skin = state.skin[id] || '';
  view.name = skin ? `${id}__${skin}` : id;
  viewer.message(keepView ? '…' : 'Preparing a preview (the first time takes a few seconds)…');
  let r;
  try { r = await api(`/api/preview?${q({ id, skin })}`); }
  catch (e) { viewer.message('Preview failed: ' + e.message); return; }
  if (view.car !== id || $('#viewer').hidden) return;
  await viewer.load(previewUrl(r.url), { keepView });
}

function renderSkins() {
  const d = state.details[view.car];
  const strip = $('#viewerSkins');
  if (!d || d.skins.length < 2) { strip.replaceChildren(); return; }
  strip.replaceChildren(...d.skins.map((x) => h('button', {
    class: 'strip-skin' + (state.skin[d.id] === x.id ? ' on' : ''), title: x.name, 'aria-label': x.name,
    onclick: () => { state.skin[d.id] = x.id; renderSkins(); loadPreview(true); emit('detail'); },
  }, x.preview ? img(fileUrl(d.id, x.preview)) : h('span', {}, x.name))));
}

// ---------------------------------------------------------------- paint and paint parts

function applyPaint() {
  const n = viewer.setPaint(paintOn ? $('#paintPick').value : null, segValue('#paintMode'));
  $('#paintInfo').textContent = n ? (paintOn ? `${n} ✓` : '') : `${t('Paint parts')}: 0 → ${t('Parts…')}`;
}

export function setPaintColour(hex, mode = 'tint') {
  $('#paintPick').value = hex;
  $$('#paintMode button').forEach((b) => b.classList.toggle('on', b.dataset.p === mode));
  paintOn = true;
  applyPaint();
}

function currentParts() {
  const chosen = view.car ? state.paintNames[view.car] : null;
  return chosen || viewer.materials().filter((m) => m.paint).map((m) => m.name);
}

// Which materials count as paint: the converter's guess, or the user's choice for this car
function renderParts() {
  const list = $('#partsList');
  const chosen = new Set(currentParts());
  list.replaceChildren(
    h('div', { class: 'parts-head' }, h('b', {}, t('Paint parts')),
      h('span', { class: 'muted' }, t('Click a part of the car to add or remove it')),
      h('span', { class: 'muted' }, t(view.car ? 'Used for the next export of this car too' : 'Only in this viewer'))),
    h('div', { class: 'parts-items' }, ...viewer.materials().map((m) => h('label', { class: 'check' },
      h('input', { type: 'checkbox', value: m.name, checked: chosen.has(m.name), onchange: partsChanged }),
      h('span', { title: m.name }, m.name),
      m.paint && h('span', { class: 'flag-pill info' }, 'auto'),
      m.kind && h('span', { class: 'flag-pill info' }, m.kind)))),
    h('button', { class: 'btn ghost small', onclick: () => setParts(null) }, t('Back to automatic')));
}

function partsChanged() {
  setParts($$('#partsList input:checked').map((x) => x.value));
}

function setParts(names) {
  if (view.car) {
    if (names) state.paintNames[view.car] = names;
    else delete state.paintNames[view.car];
    emit('detail');
  }
  viewer.setPaintNames(names);
  if (!names) renderParts();
  if (!paintOn) { paintOn = true; }
  applyPaint();
}

// ---------------------------------------------------------------- tools

function renderAnimations(names = []) {
  const sel = $('#animSel');
  sel.hidden = !names.length;
  sel.replaceChildren(h('option', { value: '' }, t('Animations ({n})', { n: names.length })),
    ...names.map((n) => h('option', { value: n }, n)));
}

function setMeasure(on) {
  $('#measureBtn').classList.toggle('on', on);
  $('#measureBtn').setAttribute('aria-pressed', String(on));
  $('#measureInfo').textContent = on ? t('Click two points on the car') : '';
  viewer.setMeasuring?.(on);
}

export function setLights(on) {
  $('#lightsBtn').classList.toggle('on', on);
  $('#lightsBtn').setAttribute('aria-pressed', String(on));
  viewer.setLights(on);
}

export function setStudio(name) {
  $$('#studioSeg button').forEach((b) => b.classList.toggle('on', b.dataset.s === name));
  prefs.set('studio', name);
  if (viewer.renderer) viewer.setStudio(name);
  else viewer.studio = name;
}

export function setSteer(deg) {
  $('#steerRange').value = deg;
  viewer.setSteer(Number(deg));
}

export function setMode(mode) {
  $$('#shadeSeg button').forEach((b) => b.classList.toggle('on', b.dataset.m === mode));
  viewer.setMode(mode);
}

// ---------------------------------------------------------------- pictures and video

const upload = (body) => api('/api/render', { name: view.name || 'render', ...body });
const showFolder = () => ({ label: t('Show folder'), run: () => api('/api/open', { what: 'renders', name: view.name }) });

async function runSnapshot() {
  const data = viewer.snapshot();
  if (!data) return;
  const index = (prefs.get('snapIndex', 0) + 1) % 10000;
  prefs.set('snapIndex', index);
  try {
    const r = await upload({ index, data, snapshot: true });
    flash(r.file, false, showFolder());
  } catch (e) { flash(e.message, true); }
}

async function runTurntable() {
  const btn = $('#turntableBtn');
  btn.disabled = true;
  try {
    let folder = null;
    await viewer.turntable({
      frames: 36,
      onFrame: async (i, data) => {
        btn.textContent = `${i + 1}/36`;
        folder = (await upload({ index: i, data })).folder;
      },
    });
    flash(`36 × PNG → ${displayPath(folder, state.settings.home)}`, false, showFolder());
  } catch (e) { flash(e.message, true); }
  finally { btn.disabled = false; btn.textContent = t('Turntable'); }
}

function blobToDataUrl(blob) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(blob);
  });
}

async function runVideo() {
  const btn = $('#videoBtn');
  btn.disabled = true;
  btn.textContent = '● REC';
  try {
    const blob = await viewer.recordVideo(8);
    btn.textContent = '…';
    const data = (await blobToDataUrl(blob)).replace(/^data:video\/webm;[^,]*,/, 'data:video/webm;base64,');
    const r = await upload({ data });
    flash(`WebM → ${displayPath(r.folder, state.settings.home)}`, false, showFolder());
  } catch (e) { flash(e.message, true); }
  finally { btn.disabled = false; btn.textContent = t('Video'); }
}

// ---------------------------------------------------------------- wiring

export function closeViewer() {
  $('#viewer').hidden = true;
  emit('viewer-closed');
  setMeasure(false);
  viewer.stopAnimations();
  viewer.stop();
}

export const viewerOpen = () => !$('#viewer').hidden;
export const partsOpen = () => !$('#partsList').hidden;
export function closeParts() { $('#partsList').hidden = true; }

export function initViewer() {
  viewer.studio = prefs.get('studio', 'studio');
  $$('#studioSeg button').forEach((b) => b.classList.toggle('on', b.dataset.s === viewer.studio));
  $('#viewerClose').onclick = closeViewer;
  $('#resetView').onclick = () => viewer.reset();
  wireSeg('#shadeSeg', (b) => viewer.setMode(b.dataset.m));
  wireSeg('#studioSeg', (b) => setStudio(b.dataset.s));
  wireSeg('#paintMode', () => { if (paintOn) applyPaint(); });
  $('#paintPick').addEventListener('input', () => { paintOn = true; applyPaint(); });
  $('#paintReset').onclick = () => { paintOn = false; applyPaint(); };
  $('#partsBtn').onclick = () => { $('#partsList').hidden = !$('#partsList').hidden; if (!$('#partsList').hidden) renderParts(); };
  $('#steerRange').addEventListener('input', (e) => viewer.setSteer(Number(e.target.value)));
  $('#steerRange').addEventListener('dblclick', () => setSteer(0));
  $('#lightsBtn').onclick = () => setLights(!$('#lightsBtn').classList.contains('on'));
  $('#measureBtn').onclick = () => setMeasure(!$('#measureBtn').classList.contains('on'));
  $('#animSel').onchange = (e) => { if (e.target.value) viewer.playAnimation(e.target.value); else viewer.stopAnimations(); };
  $('#snapBtn').onclick = runSnapshot;
  $('#turntableBtn').onclick = runTurntable;
  $('#videoBtn').onclick = runVideo;
  $('#viewerBlender').onclick = () => view.file && openInBlender(view.file);
}
