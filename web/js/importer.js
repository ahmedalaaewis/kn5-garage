// Import: install downloaded mods and skin packs, open or convert 3D files, and make a new
// car from a 3D model (beta). Files are chosen in Windows' own file dialog by the server.

import { api } from './api.js';
import { $, h } from './dom.js';
import { t } from './i18n.js';
import { displayPath } from './model.js';
import { openParts } from './parts.js';
import { emit, state } from './state.js';
import { flash } from './toast.js';
import { openViewer } from './viewer-ui.js';

let mod = null;  // the archive being installed: what the server found in it

const showPath = (p) => displayPath(p, state.settings.home);
const editable = () => state.cars.filter((c) => c.status !== 'protected' && c.status !== 'encrypted' && c.kn5);

function busy(btn, on) {
  btn.disabled = on;
}

export function openImport() {
  mod = null;
  render();
  $('#importDlg').hidden = false;
}

function card(title, text, ...body) {
  return h('div', { class: 'edit-section import-card' }, h('h4', {}, title), h('span', { class: 'muted' }, text), ...body);
}

function render() {
  $('#importBody').replaceChildren(...(mod ? modView() : menu()));
}

function menu() {
  const fmt = h('select', { class: 'select small', 'aria-label': t('Format') },
    h('option', { value: 'glb' }, t('View it (.glb)')), h('option', { value: 'obj' }, '.obj'), h('option', { value: 'stl' }, '.stl'),
    h('option', { value: 'fbx' }, '.fbx (Blender)'));
  const donor = h('select', { class: 'select small', 'aria-label': t('Donor car') },
    ...editable().map((c) => h('option', { value: c.id }, c.name || c.id)));
  if (state.selected && editable().some((c) => c.id === state.selected)) donor.value = state.selected;
  const rotate = h('select', { class: 'select small', 'aria-label': t('Turn the model') },
    ...[0, 90, 180, 270].map((d) => h('option', { value: d }, `${d}°`)));
  const zUp = h('input', { type: 'checkbox' });
  return [
    card(t('Install a mod or skin pack'), t('A .zip, .7z or .rar you downloaded. Cars go to your cars folder; skins to their car. Programs inside are never unpacked.'),
      h('div', { class: 'edit-row end' }, h('button', { class: 'btn primary small', onclick: (e) => chooseArchive(e.target) }, t('Choose file…')))),
    card(t('Open a 3D file'), t('.glb, .gltf, .obj, .fbx or .kn5: look at it here, or save it in another format to the output folder.'),
      h('div', { class: 'edit-row' }, fmt, h('span', { class: 'tool-gap' }),
        h('button', { class: 'btn small', onclick: (e) => openModel(e.target, fmt.value) }, t('Choose file…')))),
    card(t('Make a car from a 3D model (beta)'),
      t('The new car takes the physics, sound, wheels and driver of a donor car, and the 3D model replaces its body. You can adjust it in the parts editor before saving.'),
      h('div', { class: 'edit-grid' },
        h('label', { class: 'edit-field wide' }, h('span', {}, t('Donor car')), donor),
        h('label', { class: 'edit-field' }, h('span', {}, t('Turn the model')), rotate),
        h('label', { class: 'check' }, zUp, h('span', {}, t('The model is Z-up')))),
      h('div', { class: 'edit-row end' }, h('button', {
        class: 'btn small', disabled: !editable().length,
        onclick: (e) => buildCar(e.target, donor.value, Number(rotate.value), zUp.checked),
      }, t('Choose model…')))),
  ];
}

// ---------------------------------------------------------------- mods

async function chooseArchive(btn) {
  busy(btn, true);
  try {
    const r = await api('/api/import/archive', {});
    if (!r.import) return;
    mod = r;
    render();
  } catch (e) { flash(e.message, true); }
  finally { busy(btn, false); }
}

function modView() {
  const known = new Set(state.cars.map((c) => c.id));
  const dest = h('select', { class: 'select small', id: 'impDest', 'aria-label': t('Install to') },
    ...state.roots.map((r) => h('option', { value: r }, showPath(r))), h('option', { value: 'pick' }, t('Choose a folder…')));
  const conflict = h('select', { class: 'select small', id: 'impConflict', 'aria-label': t('If it is already there') },
    h('option', { value: 'skip' }, t('Skip it')), h('option', { value: 'rename' }, t('Install next to it (new name)')),
    h('option', { value: 'replace' }, t('Replace it (the old one goes to the backups)')));
  return [
    h('div', { class: 'parts-head' }, h('b', {}, mod.name),
      mod.skippedCount ? h('span', { class: 'muted' }, t('{n} program files were left out', { n: mod.skippedCount })) : null,
      ...mod.notes.map((n) => h('span', { class: 'muted' }, t(n)))),
    mod.cars.length ? h('h5', {}, t('Cars')) : null,
    ...mod.cars.map((c) => h('label', { class: 'check import-item' },
      h('input', { type: 'checkbox', checked: true, 'data-car': c.path, disabled: !c.validId }),
      h('span', {}, c.name), h('span', { class: 'muted' }, c.id),
      known.has(c.id) && h('span', { class: 'flag-pill issue' }, t('Already in your garage')),
      c.status === 'protected' && h('span', { class: 'flag-pill info' }, t('Protected')))),
    mod.skins.length ? h('h5', {}, t('Skins')) : null,
    ...mod.skins.map((s) => h('div', { class: 'import-item edit-row' },
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: true, 'data-skin': s.path }), h('span', {}, s.name)),
      h('span', { class: 'tool-gap' }),
      h('select', { class: 'select small', 'data-skin-car': s.path, 'aria-label': t('Car') },
        ...(s.car ? [h('option', { value: s.car }, s.car)] : [h('option', { value: '' }, t('Choose its car…'))]),
        ...state.cars.filter((c) => c.id !== s.car).map((c) => h('option', { value: c.id }, c.name || c.id))))),
    h('div', { class: 'edit-grid' },
      h('label', { class: 'edit-field wide' }, h('span', {}, t('Install to')), dest),
      h('label', { class: 'edit-field wide' }, h('span', {}, t('If it is already there')), conflict)),
    h('div', { class: 'edit-row end' },
      h('button', { class: 'btn ghost small', onclick: () => { mod = null; render(); } }, t('Back')),
      h('button', { class: 'btn primary small', onclick: (e) => install(e.target) }, t('Install'))),
  ];
}

async function install(btn) {
  const body = $('#importBody');
  const cars = [...body.querySelectorAll('[data-car]')].filter((x) => x.checked).map((x) => x.dataset.car);
  const skins = [...body.querySelectorAll('[data-skin]')].filter((x) => x.checked).map((x) => x.dataset.skin);
  const skinCars = {};
  body.querySelectorAll('[data-skin-car]').forEach((x) => { if (x.value) skinCars[x.dataset.skinCar] = x.value; });
  if (!cars.length && !skins.length) return flash(t('Choose something to install.'), true);
  busy(btn, true);
  try {
    const r = await api('/api/import/install', { import: mod.import, cars, skins, skinCars, dest: $('#impDest').value,
      conflict: $('#impConflict').value });
    if (r.cancelled) return;
    $('#importDlg').hidden = true;
    const done = t('Installed {n} item(s) in {folder}', { n: r.installed.length, folder: showPath(r.folder) });
    flash([done, ...r.notes].join(' · '), !r.installed.length);
    emit('rescan');
  } catch (e) { flash(e.message, true); }
  finally { busy(btn, false); }
}

// ---------------------------------------------------------------- 3D files

async function openModel(btn, format) {
  busy(btn, true);
  try {
    const r = await api('/api/import/model', { format });
    if (!r.file) return;
    $('#importDlg').hidden = true;
    if (r.format === 'glb') await openViewer(r.file, r.file.replace(/_imported\.glb$/, ''));
    else flash(t('Saved {file}', { file: r.file }), false, { label: t('Show folder'), run: () => api('/api/open', { what: 'out' }) });
  } catch (e) { flash(e.message, true); }
  finally { busy(btn, false); }
}

async function buildCar(btn, donor, rotate, zUp) {
  busy(btn, true);
  try {
    const r = await api('/api/build/start', { donor, rotate, zUp });
    if (!r.session) return;
    $('#importDlg').hidden = true;
    await openParts(donor, r);
    flash(t('{model} is on the car. Adjust it, then Save as a new car.', { model: r.model }), false);
  } catch (e) { flash(e.message, true); }
  finally { busy(btn, false); }
}

export function initImport() {
  $('#importBtn').onclick = openImport;
}
