// Parts editor: hide, remove, move / rotate / scale and recolour a car's parts, or swap in parts
// from another car or a 3D file. The server keeps the list of changes (with undo / redo) and
// writes the car only on Save; quick changes show at once here, the rest reload the preview.

import { api, previewUrl } from './api.js';
import { $, h } from './dom.js';
import { afterSave, chooseTarget } from './editor.js';
import { t } from './i18n.js';
import { on, state } from './state.js';
import { flash } from './toast.js';
import { openPreview, viewer } from './viewer-ui.js';

const MAX_ROWS = 400;
let s = null;  // {id, session, parts, materials, selected, filter, source, sourceFilter, busy}

// started: a session the server already opened (a new car from a 3D model)
export async function openParts(id, started = null) {
  await openPreview(id);
  let r = started;
  try {
    viewer.message(t('Opening the parts editor…'));
    r = r || await api('/api/parts/start', { id, skin: state.skin[id] || null });
  } catch (e) { viewer.message(null); flash(e.message, true); return; }
  s = { id, selected: null, filter: '', source: null, sourceFilter: '', busy: false };
  $('#liveryPanel').hidden = true;
  $('#partsPanel').hidden = false;
  await take(r);
}

export function closeParts() {
  if (s) api('/api/parts/close', { session: s.session }).catch(() => {});
  s = null;
  $('#partsPanel').hidden = true;
  viewer.select(null);
}

// A new state from the server; reload the 3D preview when it says so
async function take(r) {
  Object.assign(s, { session: r.session, parts: r.parts, materials: r.materials, canUndo: r.canUndo,
    canRedo: r.canRedo, steps: r.steps });
  if (r.url) await viewer.load(previewUrl(r.url), { keepView: true });
  render();
  viewer.select(s.parts[s.selected]?.removed ? null : s.selected);
}

async function send(body) {
  if (!s || s.busy) return;
  s.busy = true;
  const st = $('#partsStatus');
  if (st) st.textContent = t('Working…');
  try { await take(await api('/api/parts/step', { id: s.id, session: s.session, ...body })); }
  catch (e) { flash(e.message, true); render(); }
  finally { if (s) s.busy = false; }
}

const step = (x) => send({ step: x });

// ---------------------------------------------------------------- transforms

function THREE() { return viewer.T.THREE; }

function matrixOf(p) {
  return new (THREE().Matrix4)().fromArray(p.group ? p.matrix : p.baked);
}

// what the viewer shows for a mesh is already moved by `baked`, so it gets the difference
function showMatrix(p, m) {
  const shown = p.group ? m : m.clone().multiply(matrixOf(p).invert());
  viewer.setNodeMatrix(p.i, shown.toArray());
}

function readTransform() {
  const T = THREE();
  const n = (id) => Number($(id).value) || 0;
  const pos = new T.Vector3(n('#ptX'), n('#ptY'), n('#ptZ'));
  const rad = Math.PI / 180;
  const rot = new T.Quaternion().setFromEuler(new T.Euler(n('#ptRX') * rad, n('#ptRY') * rad, n('#ptRZ') * rad));
  const k = n('#ptS') || 1;
  return new T.Matrix4().compose(pos, rot, new T.Vector3(k, k, k));
}

function transformFields(p) {
  const T = THREE();
  const pos = new T.Vector3(), quat = new T.Quaternion(), scale = new T.Vector3();
  matrixOf(p).decompose(pos, quat, scale);
  const e = new T.Euler().setFromQuaternion(quat);
  const deg = (r) => Math.round((r * 180) / Math.PI * 100) / 100;
  const num = (id, label, value, stepSize) => h('label', { class: 'edit-field' }, h('span', {}, label),
    h('input', { class: 'search small-input', type: 'number', id, value: Math.round(value * 1000) / 1000, step: stepSize,
      oninput: () => showMatrix(p, readTransform()) }));
  return h('div', { class: 'parts-move' },
    h('h5', {}, t('Move, rotate, scale')),
    h('div', { class: 'edit-grid three' },
      num('ptX', 'X (m)', pos.x, 0.01), num('ptY', 'Y (m)', pos.y, 0.01), num('ptZ', 'Z (m)', pos.z, 0.01),
      num('ptRX', t('Turn X (°)'), deg(e.x), 1), num('ptRY', t('Turn Y (°)'), deg(e.y), 1), num('ptRZ', t('Turn Z (°)'), deg(e.z), 1),
      num('ptS', t('Size (×)'), (scale.x + scale.y + scale.z) / 3, 0.01)),
    h('div', { class: 'edit-row end' },
      h('button', { class: 'btn ghost small', onclick: () => { showMatrix(p, matrixOf(p)); render(); } }, t('Reset')),
      h('button', { class: 'btn small primary', onclick: () => step({ op: 'move', node: p.i, matrix: readTransform().toArray() }) },
        t('Apply'))));
}

// ---------------------------------------------------------------- panel

function partRow(p) {
  return h('div', {
    class: 'part-row' + (p.i === s.selected ? ' on' : '') + (p.removed ? ' removed' : ''),
    style: `padding-inline-start: ${Math.min(p.depth, 12) * 10 + 6}px`,
    onclick: () => choose(p.i),
  },
  h('input', { type: 'checkbox', checked: p.active && !p.removed, disabled: p.removed, title: t('Show'),
    'aria-label': t('Show'), onclick: (e) => e.stopPropagation(),
    onchange: (e) => { viewer.setNodeVisible(p.i, e.target.checked); step({ op: 'show', node: p.i, on: e.target.checked }); } }),
  h('span', { class: 'part-name', title: p.name }, p.group ? `▸ ${p.name}` : p.name),
  p.material && h('span', { class: 'muted part-mat' }, p.material));
}

function choose(i) {
  s.selected = i;
  viewer.select(i);
  render();
  document.querySelector('.part-row.on')?.scrollIntoView({ block: 'nearest' });
}

function selectedSection() {
  const p = s.parts[s.selected];
  if (!p) return h('div', { class: 'muted small-note' }, t('Click a part on the car, or in the list, to edit it.'));
  if (p.removed) {
    return h('div', { class: 'edit-section' }, h('b', {}, p.name), h('span', { class: 'muted' }, t('Removed (Undo brings it back)')));
  }
  return h('div', { class: 'edit-section' },
    h('div', { class: 'parts-sel-head' }, h('b', { title: p.name }, p.name),
      h('span', { class: 'muted' }, p.group ? t('Group of parts') : `${p.material} · ${t('{n} vertices', { n: p.verts })}`)),
    h('div', { class: 'edit-row' },
      h('button', { class: 'btn small', onclick: () => { viewer.setNodeVisible(p.i, !p.active); step({ op: 'show', node: p.i, on: !p.active }); } },
        p.active ? t('Hide') : t('Show')),
      p.i > 0 && h('button', { class: 'btn small danger', onclick: () => { viewer.setNodeVisible(p.i, false); step({ op: 'remove', node: p.i }); } },
        t('Remove'))),
    !p.rigged && p.i > 0 && transformFields(p),
    !p.group && colourFields(p),
    !p.group && materialField(p),
    p.i > 0 && swapFields(p));
}

function colourFields(p) {
  return h('div', { class: 'parts-colour' },
    h('h5', {}, t('Colour')),
    h('div', { class: 'edit-row' },
      h('input', { type: 'color', id: 'ptColour', value: '#c8102e', 'aria-label': t('Colour') }),
      h('select', { class: 'select small', id: 'ptColourMode', 'aria-label': t('How') },
        h('option', { value: 'tint' }, t('Tint (keeps the details)')), h('option', { value: 'solid' }, t('Solid colour'))),
      h('select', { class: 'select small', id: 'ptColourScope', 'aria-label': t('Which parts') },
        h('option', { value: 'part' }, t('Only this part')), h('option', { value: 'material' }, t('Every part with this material')))),
    h('div', { class: 'edit-row end' }, h('button', {
      class: 'btn small primary',
      onclick: () => step({ op: 'colour', node: p.i, colour: $('#ptColour').value, mode: $('#ptColourMode').value, scope: $('#ptColourScope').value }),
    }, t('Apply colour'))));
}

function materialField(p) {
  const sel = h('select', { class: 'select small', 'aria-label': t('Material') },
    ...s.materials.map((m) => h('option', { value: m.i }, m.name)));
  sel.value = String(s.materials.find((m) => m.name === p.material)?.i ?? 0);
  return h('div', {},
    h('h5', {}, t('Material')),
    h('div', { class: 'edit-row' }, sel,
      h('button', { class: 'btn small', onclick: () => step({ op: 'material', node: p.i, material: Number(sel.value) }) }, t('Use'))));
}

// ---------------------------------------------------------------- parts from elsewhere

async function openSource(car) {
  try {
    const r = await api('/api/parts/source', { id: s.id, session: s.session, ...(car ? { car } : {}) });
    if (!r.source) return;
    s.source = r;
    s.sourcePart = null;
    render();
  } catch (e) { flash(e.message, true); }
}

function swapFields(p) {
  const cars = state.cars.filter((c) => c.id !== s.id && c.status !== 'protected' && c.kn5);
  const carSel = h('select', { class: 'select small', 'aria-label': t('Take a part from') },
    h('option', { value: '' }, t('Another car…')), ...cars.map((c) => h('option', { value: c.id }, c.name || c.id)));
  carSel.onchange = () => carSel.value && openSource(carSel.value);
  const src = s.source;
  const items = src ? src.parts.filter((x) => x.i > 0 && x.name.toLowerCase().includes(s.sourceFilter.toLowerCase())).slice(0, 200) : [];
  return h('div', { class: 'parts-swap' },
    h('h5', {}, t('Swap in a part')),
    h('div', { class: 'edit-row' }, carSel,
      h('button', { class: 'btn small ghost', onclick: () => openSource(null) }, t('3D file…'))),
    src && h('div', { class: 'muted small-note' }, t('Parts of {name}', { name: src.name })),
    src && h('input', { class: 'search small-input', placeholder: t('Search parts'), value: s.sourceFilter,
      oninput: (e) => { s.sourceFilter = e.target.value; render(); $('#ptSourceSearch')?.focus(); }, id: 'ptSourceSearch' }),
    src && h('div', { class: 'parts-items short' }, ...items.map((x) => h('div', {
      class: 'part-row' + (x.i === s.sourcePart ? ' on' : ''), onclick: () => { s.sourcePart = x.i; render(); },
      style: `padding-inline-start: ${Math.min(x.depth, 12) * 10 + 6}px`,
    }, h('span', { class: 'part-name', title: x.name }, x.group ? `▸ ${x.name}` : x.name)))),
    src && h('label', { class: 'check' }, h('input', { type: 'checkbox', id: 'ptFit', checked: true }),
      h('span', {}, t('Fit it to the size and place of the selected part'))),
    src && h('div', { class: 'edit-row end' },
      h('button', { class: 'btn small', disabled: s.sourcePart == null,
        onclick: () => step({ op: 'add', source: src.source, node: s.sourcePart, replace: null }) }, t('Add')),
      h('button', { class: 'btn small primary', disabled: s.sourcePart == null,
        onclick: () => step({ op: 'add', source: src.source, node: s.sourcePart, replace: p.i, fit: $('#ptFit').checked }) },
        t('Replace selected'))));
}

// ---------------------------------------------------------------- save

async function save() {
  if (!s.steps) return flash(t('Nothing has changed yet.'), true);
  let blockers = [];
  try { blockers = (await api(`/api/edit/info?id=${encodeURIComponent(s.id)}`)).copyBlockers; } catch (e) { /* shown on save */ }
  const target = await chooseTarget(s.id, blockers, t('Edited parts'));
  if (!target) return;
  try {
    const r = await api('/api/parts/save', { id: s.id, session: s.session, target });
    const id = s.id;
    s = null;
    $('#partsPanel').hidden = true;
    viewer.select(null);
    afterSave(r, id);
  } catch (e) { flash(e.message, true); }
}

function render() {
  if (!s) return;
  const f = s.filter.toLowerCase();
  const rows = s.parts.filter((p) => !f || p.name.toLowerCase().includes(f) || (p.material || '').toLowerCase().includes(f));
  $('#partsPanel').replaceChildren(
    h('div', { class: 'parts-head' }, h('b', {}, t('Edit parts')),
      h('span', { class: 'muted' }, t('Changes stay here until you save.'))),
    h('div', { class: 'edit-row' },
      h('button', { class: 'btn small ghost', disabled: !s.canUndo, onclick: () => send({ undo: true }) }, t('Undo')),
      h('button', { class: 'btn small ghost', disabled: !s.canRedo, onclick: () => send({ redo: true }) }, t('Redo')),
      h('span', { class: 'muted small-note', id: 'partsStatus' }, t('{n} changes', { n: s.steps }))),
    h('input', { class: 'search small-input', id: 'ptSearch', placeholder: t('Search parts'), value: s.filter,
      oninput: (e) => { s.filter = e.target.value; render(); $('#ptSearch').focus(); } }),
    h('div', { class: 'parts-items' }, ...rows.slice(0, MAX_ROWS).map(partRow),
      rows.length > MAX_ROWS && h('div', { class: 'muted small-note' }, t('{n} more: search to find them', { n: rows.length - MAX_ROWS }))),
    selectedSection(),
    h('div', { class: 'edit-row end' },
      h('button', { class: 'btn ghost small', onclick: closeParts }, t('Close')),
      h('button', { class: 'btn primary small', onclick: save }, t('Save…'))));
}

export function initParts() {
  on('open-parts', openParts);
  on('viewer-pick', (part) => { if (s && part.node != null) choose(part.node); });
  on('viewer-closed', () => { if (s) closeParts(); });
}
