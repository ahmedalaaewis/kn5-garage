// The Export dialog (presets, paint, lights, textures, saved presets), running export
// jobs (progress, cancel) and the Exports drawer.

import { api, q } from './api.js';
import { $, $$, fmtBytes, h, segValue, wireSeg } from './dom.js';
import { t } from './i18n.js';
import { isBad } from './model.js';
import { carById, emit, on, state } from './state.js';
import { flash, jobProgress, jobStarted } from './toast.js';
import { openViewer } from './viewer-ui.js';

const GEOMETRY_ONLY = new Set(['print']);       // no textures, paint or lights
const NO_BLENDER_SCRIPT = new Set(['print', 'obj', 'fbx']);

// ---------------------------------------------------------------- dialog

export function openExportDialog(ids, options = null) {
  state.exportIds = ids;
  if (options) applyOptions(options);
  const one = ids.length === 1 ? carById(ids[0]) : null;
  $('#exportTitle').textContent = one ? t('Export {name}', { name: one.name }) : t('Export {n} cars', { n: ids.length });
  renderPresets();
  renderSaved();
  updateDialog();
  $('#exportDlg').hidden = false;
  $('#exportGo').focus();
}

function renderPresets() {
  const presets = state.settings.presets || { render: 'Blender render (full quality)' };
  if (!(state.preset in presets)) state.preset = Object.keys(presets)[0];
  $('#presetList').replaceChildren(...Object.entries(presets).map(([k, label]) => {
    const needsBlender = k === 'fbx' && !state.blender.found;
    return h('button', {
      class: 'preset' + (state.preset === k ? ' on' : ''), role: 'radio', 'aria-checked': String(state.preset === k),
      disabled: needsBlender, title: needsBlender ? 'Needs Blender: click "Blender" in the top bar to locate it' : null,
      onclick: () => { state.preset = k; renderPresets(); updateDialog(); },
    }, h('b', {}, k), label);
  }));
}

function updateDialog() {
  const ids = state.exportIds;
  const one = ids.length === 1 ? carById(ids[0]) : null;
  const geo = GEOMETRY_ONLY.has(state.preset);
  for (const id of ['#paintOn', '#lightsOnExp', '#texSize', '#jpegOn']) $(id).disabled = geo;
  $('#blenderScript').disabled = NO_BLENDER_SCRIPT.has(state.preset);
  $('#paintRow').hidden = geo || !$('#paintOn').checked;
  const notes = [];
  if (state.preset === 'game' && one && !one.hasLod) notes.push('This car has no LOD model; the full model is used.');
  if (state.preset === 'print') notes.push('STL has no textures or colours; it is one mesh in millimetres.');
  if (state.preset === 'obj') notes.push('OBJ comes with a .mtl file and a textures folder; animations are left out.');
  if (state.preset === 'fbx') notes.push('Blender converts the export to FBX in the background (textures embedded).');
  if (one?.status === 'protected') notes.push('Protected car: only the shape exports correctly.');
  const parts = one && state.paintNames[one.id];
  if (!geo && parts?.length) notes.push(`${t('Paint parts')}: ${parts.join(', ')}.`);
  $('#exportNote').textContent = notes.join(' ');
}

function currentOptions() {
  const geo = GEOMETRY_ONLY.has(state.preset);
  const size = $('#texSize').value;
  return {
    preset: state.preset,
    paint: !geo && $('#paintOn').checked ? $('#exportPaint').value : null,
    paintMode: segValue('#exportPaintMode'),
    lightsOn: !geo && $('#lightsOnExp').checked,
    textureSize: !geo && size ? Number(size) : null,
    jpegQuality: !geo && $('#jpegOn').checked ? 85 : null,
    blenderScript: !NO_BLENDER_SCRIPT.has(state.preset) && $('#blenderScript').checked,
  };
}

function applyOptions(o) {
  if (o.preset && o.preset in (state.settings.presets || {})) state.preset = o.preset;
  $('#paintOn').checked = !!o.paint;
  if (o.paint) $('#exportPaint').value = o.paint;
  $$('#exportPaintMode button').forEach((b) => b.classList.toggle('on', b.dataset.p === (o.paintMode || 'tint')));
  $('#lightsOnExp').checked = !!o.lightsOn;
  $('#texSize').value = o.textureSize ? String(o.textureSize) : '';
  $('#jpegOn').checked = !!o.jpegQuality;
  $('#blenderScript').checked = o.blenderScript !== false;
}

function renderSaved() {
  const saved = state.settings.savedPresets || [];
  $('#savedSel').replaceChildren(h('option', { value: '' }, `${t('Saved presets')} (${saved.length})`),
    ...saved.map((p) => h('option', { value: p.name }, p.name)));
  $('#savedSel').hidden = !saved.length;
  $('#savedDelete').hidden = true;
}

async function savePreset() {
  const name = $('#saveName').value.trim();
  if (!name) { $('#saveName').focus(); return; }
  try {
    const r = await api('/api/presets/save', { name, options: currentOptions() });
    state.settings.savedPresets = r.savedPresets;
    $('#saveName').value = '';
    renderSaved();
    $('#savedSel').value = name;
    $('#savedDelete').hidden = false;
  } catch (e) { flash(e.message, true); }
}

async function deletePreset() {
  try {
    const r = await api('/api/presets/delete', { name: $('#savedSel').value });
    state.settings.savedPresets = r.savedPresets;
    renderSaved();
  } catch (e) { flash(e.message, true); }
}

function startExport() {
  const ids = state.exportIds;
  const parts = ids.length === 1 ? state.paintNames[ids[0]] : null;
  const o = currentOptions();
  $('#exportDlg').hidden = true;
  exportCars(ids, ids.length === 1 ? state.skin[ids[0]] : null, {
    ...o, ...(parts?.length && !GEOMETRY_ONLY.has(o.preset) ? { paintMaterials: parts } : {}),
  });
}

// ---------------------------------------------------------------- jobs

export async function exportCars(ids, skin, opts = {}) {
  const body = Object.fromEntries(Object.entries({ ids, skin: ids.length === 1 ? skin : null, ...opts })
    .filter(([, v]) => v !== null && v !== undefined));
  let job;
  try { job = (await api('/api/convert', body)).job; }
  catch (e) { flash(e.message, true); return; }
  for (const id of ids) state.carJobs[id] = { job, fraction: 0, message: 'Queued', finished: false };
  jobStarted(ids.length, () => api('/api/job/cancel', { id: job }).catch((e) => flash(e.message, true)));
  emit('detail');
  poll(job, ids);
}

async function poll(job, ids) {
  let j;
  try { j = await api(`/api/job?${q({ id: job })}`); }
  catch (e) { flash(e.message, true); return; }
  jobProgress(j);
  for (const id of ids) {
    const cj = state.carJobs[id];
    if (!cj || cj.job !== job) continue;
    const result = j.results.find((r) => r.id === id);
    if (result) {
      cj.finished = true;
      cj.result = result;
      if (result.ok) state.done[id] = result;
    } else if (j.current === id) {
      cj.fraction = j.total === 1 ? j.fraction : (j.fraction * j.total - j.done);
      cj.message = j.message;
    }
  }
  if (state.selected && ids.includes(state.selected)) emit('job-progress', state.selected);
  if (j.state === 'done') {
    emit('grid');
    refreshExports();
    emit('history');
    return;
  }
  setTimeout(() => poll(job, ids), 350);
}

// ---------------------------------------------------------------- exports drawer

export async function refreshExports() {
  try {
    const r = await api('/api/outputs');
    state.exports = r.files;
    $('#exportsCount').textContent = r.files.length;
    $('#exportsList').replaceChildren(...(r.files.length ? r.files.map((f) => h('div', { class: 'export-row' },
      h('div', { class: 'grow' }, h('b', { title: f.name }, f.name),
        h('span', { class: 'muted' }, `${fmtBytes(f.size)} · ${new Date(f.modified * 1000).toLocaleString()}`)),
      f.viewable !== false
        ? h('button', { class: 'btn small', onclick: () => openViewer(f.name, f.name.replace(/\.glb$/, '')) }, t('View 3D'))
        : h('span', { class: 'flag-pill info' }, f.name.split('.').pop().toUpperCase()),
    )) : [h('p', { class: 'muted' }, t('Nothing exported yet.'))]));
  } catch (e) { /* the drawer just stays as it was */ }
}

export function initExporter() {
  $('#texSize').replaceChildren(h('option', { value: '' }, t('From the preset')),
    ...[256, 512, 1024, 2048, 4096].map((s) => h('option', { value: String(s) }, `${s} px`)));
  $('#exportGo').onclick = startExport;
  $('#paintOn').onchange = updateDialog;
  $('#saveBtn').onclick = savePreset;
  $('#saveName').addEventListener('keydown', (e) => { if (e.key === 'Enter') savePreset(); });
  $('#savedDelete').onclick = deletePreset;
  $('#savedSel').onchange = (e) => {
    const p = (state.settings.savedPresets || []).find((x) => x.name === e.target.value);
    $('#savedDelete').hidden = !p;
    if (p) { applyOptions(p.options); renderPresets(); updateDialog(); }
  };
  wireSeg('#exportPaintMode');
  $('#convertSel').onclick = () => {
    const ids = [...state.checked].filter((id) => { const c = carById(id); return c && !isBad(c); });
    if (!ids.length) return flash('None of the selected cars can be exported.', true);
    openExportDialog(ids);
  };
  $('#exportsBtn').onclick = () => { refreshExports(); $('#exports').hidden = false; };
  $('#exportsClose').onclick = () => { $('#exports').hidden = true; };
  $('#outOpen').onclick = () => api('/api/open', { what: 'out' }).catch((e) => flash(e.message, true));
  on('exports', refreshExports);
  refreshExports();
}
