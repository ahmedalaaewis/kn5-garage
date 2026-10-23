// Livery studio: the car's paint textures as PNG files, edited in any image program
// (Photoshop, Illustrator, GIMP…) with a live 3D preview, then installed as a new skin.

import { api, q, TOKEN } from './api.js';
import { $, h } from './dom.js';
import { afterSave, chooseTarget } from './editor.js';
import { t } from './i18n.js';
import { carById, on, prefs, state } from './state.js';
import { flash } from './toast.js';
import { openPreview, viewer } from './viewer-ui.js';

const enc = encodeURIComponent;
let session = null;  // {id, session, textures, changed: {file: stamp}}
let timer = 0;
let programs = [];

const fileUrl = (s, file, stamp = '') => `/api/livery/file?${q({ id: s.id, session: s.session, file })}&t=${enc(TOKEN)}&v=${stamp}`;

async function loadPrograms() {
  try { programs = (await api('/api/editors')).editors; } catch (e) { programs = []; }
}

export async function openLivery(id) {
  await openPreview(id);
  await loadPrograms();
  try {
    const r = await api('/api/livery/start', { id, skin: state.skin[id] || null,
      ...(state.paintNames[id]?.length ? { paintMaterials: state.paintNames[id] } : {}) });
    session = { id, ...r, changed: {} };
  } catch (e) { flash(e.message, true); return; }
  render();
  $('#partsPanel').hidden = true;
  $('#liveryPanel').hidden = false;
  clearInterval(timer);
  timer = setInterval(poll, 1000);
  poll();
}

export function closeLivery() {
  clearInterval(timer);
  timer = 0;
  session = null;
  $('#liveryPanel').hidden = true;
}

// Whenever an edited file is saved (in any program), show it on the car
async function poll() {
  if (!session || $('#viewer').hidden) return closeLivery();
  let files;
  try { files = (await api(`/api/livery/status?${q({ id: session.id, session: session.session })}`)).files; }
  catch (e) { return; }
  for (const f of files) {
    if (session.changed[f.file] === f.changed) continue;
    const first = !(f.file in session.changed);
    session.changed[f.file] = f.changed;
    const tex = session.textures.find((x) => x.file === f.file);
    if (!first) {
      await viewer.replaceTexture(tex.texture, fileUrl(session, f.file, f.changed));
      $('#liveryStatus').textContent = t('Updated {time}', { time: new Date().toLocaleTimeString() });
      const thumb = document.querySelector(`[data-thumb="${CSS.escape(f.file)}"]`);
      if (thumb) thumb.src = fileUrl(session, f.file, f.changed);
    }
  }
}

function programPicker() {
  const sel = h('select', { class: 'select small', 'aria-label': t('Program') },
    h('option', { value: 'default' }, t('Windows default app')),
    ...programs.map((p) => h('option', { value: p.path }, p.name)),
    h('option', { value: '+add' }, t('Add a program…')));
  sel.value = programs.some((p) => p.path === prefs.get('editor', '')) ? prefs.get('editor', '') : (programs[0]?.path || 'default');
  sel.addEventListener('change', async () => {
    if (sel.value !== '+add') return prefs.set('editor', sel.value);
    try {
      programs = (await api('/api/editors/add', {})).editors;
      render();
    } catch (e) { flash(e.message, true); render(); }
  });
  return sel;
}

async function openIn(file, program) {
  try {
    const r = await api('/api/livery/open', { id: session.id, session: session.session, file, program });
    flash(t('Opened {file}. Save it there and the car updates here.', { file: r.opened }), false);
  } catch (e) { flash(e.message, true); }
}

function render() {
  const s = session;
  if (!s) return;
  const picker = programPicker();
  const c = carById(s.id);
  $('#liveryPanel').replaceChildren(
    h('div', { class: 'parts-head' }, h('b', {}, t('New livery')),
      h('span', { class: 'muted' }, t('Edit the paint in any image program. Every time you save, the car updates here.'))),
    h('div', { class: 'livery-program' }, h('span', { class: 'tool-label' }, t('Program')), picker),
    ...s.textures.map((tex) => h('div', { class: 'livery-tex' },
      h('img', { src: fileUrl(s, tex.file), alt: tex.texture, 'data-thumb': tex.file }),
      h('div', { class: 'grow' },
        h('b', {}, tex.texture), h('span', { class: 'muted' }, `${tex.width} × ${tex.height}`),
        h('div', { class: 'edit-row' },
          h('button', { class: 'btn small primary', onclick: () => openIn(tex.file, picker.value) }, t('Edit')),
          h('button', { class: 'btn small ghost', title: t('The paint layout as lines: put it on top as a guide layer'),
            onclick: () => openIn(tex.uv, picker.value) }, t('UV guide')),
          h('button', { class: 'btn small ghost', onclick: () => usePicture(tex.file) }, t('Use a picture…')))))),
    h('div', { class: 'muted small-note', id: 'liveryStatus' }, t('Watching for changes…')),
    h('div', { class: 'livery-form' },
      h('label', { class: 'edit-field' }, h('span', {}, t('Skin name')), h('input', { class: 'search small-input', id: 'lvName', value: t('My livery'), maxlength: 60 })),
      h('label', { class: 'edit-field' }, h('span', {}, t('Folder name')), h('input', { class: 'search small-input', id: 'lvFolder', value: 'my_livery', maxlength: 64 })),
      h('label', { class: 'edit-field' }, h('span', {}, t('Driver')), h('input', { class: 'search small-input', id: 'lvDriver', maxlength: 60 })),
      h('label', { class: 'edit-field' }, h('span', {}, t('Team')), h('input', { class: 'search small-input', id: 'lvTeam', value: c?.brand ? `${c.brand} Racing` : '', maxlength: 60 })),
      h('label', { class: 'edit-field' }, h('span', {}, t('Number')), h('input', { class: 'search small-input', id: 'lvNumber', maxlength: 8 }))),
    h('div', { class: 'edit-row end' },
      h('button', { class: 'btn ghost small', onclick: closeLivery }, t('Close')),
      h('button', { class: 'btn primary small', onclick: install }, t('Install as a skin…'))),
  );
}

async function usePicture(file) {
  try {
    const r = await api('/api/livery/image', { id: session.id, session: session.session, file });
    if (r.used) flash(t('Using {file}', { file: r.used }), false);
  } catch (e) { flash(e.message, true); }
}

async function install() {
  const s = session;
  const folder = $('#lvFolder').value.trim();
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(folder)) {
    $('#lvFolder').focus();
    return flash(t('The folder name may only use letters, numbers, - and _.'), true);
  }
  let blockers = [];
  try { blockers = (await api(`/api/edit/info?${q({ id: s.id })}`)).copyBlockers; } catch (e) { /* shown on save */ }
  const target = await chooseTarget(s.id, blockers, t('New livery'));
  if (!target) return;
  const preview = viewer.snapshot({ width: 1022, height: 575, background: 0x2a3038 });
  try {
    const r = await api('/api/livery/install', {
      id: s.id, session: s.session, skinName: folder, preview, target,
      info: { name: $('#lvName').value, driver: $('#lvDriver').value, team: $('#lvTeam').value, number: $('#lvNumber').value },
    });
    state.skin[r.carId] = r.skin;
    closeLivery();
    afterSave(r, s.id);
  } catch (e) { flash(e.message, true); }
}

export function initLivery() {
  on('open-livery', openLivery);
}
