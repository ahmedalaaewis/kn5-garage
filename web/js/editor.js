// The Edit tab: specs (real physics and menu specs), livery and parts shortcuts, undo.
// Every save goes through chooseTarget(): a new car (a copy) or the original with a backup.

import { api, q } from './api.js';
import { lineChart } from './charts.js';
import { $, h } from './dom.js';
import { t } from './i18n.js';
import { carById, emit, state } from './state.js';
import { flash } from './toast.js';

// ---------------------------------------------------------------- where to save

let resolveTarget = null;

// Resolves to {mode: 'new', newId, name} | {mode: 'original'} | null (cancelled)
export function chooseTarget(carId, blockers = [], what = '') {
  const c = carById(carId);
  $('#saveTitle').textContent = what ? `${t('Save')}: ${what}` : t('Save');
  $('#saveNewId').value = `${c?.id.replace(/[^A-Za-z0-9_-]/g, '_') || 'car'}_custom`.slice(0, 64);
  $('#saveNewName').value = `${c?.name || carId} (custom)`;
  const canCopy = !blockers.length;
  $('#saveModeNew').disabled = !canCopy;
  $('#saveModeNew').checked = canCopy;
  $('#saveModeOriginal').checked = !canCopy;
  $('#saveBlocked').textContent = blockers.join(' ');
  $('#saveBlocked').hidden = canCopy;
  updateSaveDialog();
  $('#saveDlg').hidden = false;
  return new Promise((res) => { resolveTarget = res; });
}

function updateSaveDialog() {
  $('#saveNewFields').hidden = !$('#saveModeNew').checked;
}

function finishSave(ok) {
  $('#saveDlg').hidden = true;
  const res = resolveTarget;
  resolveTarget = null;
  if (!res) return;
  if (!ok) return res(null);
  res($('#saveModeNew').checked
    ? { mode: 'new', newId: $('#saveNewId').value.trim(), name: $('#saveNewName').value.trim() }
    : { mode: 'original' });
}

// After a save: show the result, rescan, and select the new car when there is one
export async function afterSave(result, carId) {
  const msg = result.mode === 'new'
    ? t('Saved as a new car: {name}', { name: result.carId })
    : t('Saved. A backup was made, so you can undo it in the Edit tab.');
  flash([msg, ...(result.notes || [])].join(' '), false);
  delete state.details[carId];
  emit('rescan', result.mode === 'new' ? result.carId : carId);
}

// ---------------------------------------------------------------- the Edit tab

const cache = {};  // car id -> /api/edit/info

export function editTab(d) {
  const box = h('div', { class: 'tab-panel edit-panel' }, h('div', { class: 'muted' }, h('span', { class: 'spinner' })));
  loadInfo(d.id).then((info) => box.replaceChildren(...renderEdit(d, info)))
    .catch((e) => box.replaceChildren(h('div', { class: 'notice bad' }, e.message)));
  return box;
}

async function loadInfo(id, fresh = false) {
  if (fresh || !cache[id]) cache[id] = await api(`/api/edit/info?${q({ id })}`);
  return cache[id];
}

function renderEdit(d, info) {
  if (info.protected) {
    return [h('div', { class: 'notice protected' }, h('div', {}, h('b', {}, t('Protected')),
      t('Protected cars can’t be edited: their files are encrypted by the author.')))];
  }
  return [
    h('div', { class: 'edit-actions' },
      h('button', { class: 'btn', onclick: () => emit('open-livery', d.id) }, t('New livery…')),
      h('button', { class: 'btn', onclick: () => emit('open-parts', d.id) }, t('Edit parts…'))),
    specsEditor(d, info),
    backupsList(d, info),
  ];
}

// ---------------------------------------------------------------- specs

function field(label, value, attrs = {}) {
  return h('label', { class: 'edit-field' }, h('span', {}, label),
    h('input', { class: 'search small-input', type: 'number', value: value ?? '', ...attrs }));
}

function specsEditor(d, info) {
  const p = info.physics;
  const wrap = h('div', { class: 'edit-section' }, h('h4', {}, t('Specs')));
  const physOk = p.available;
  let scale = 100;
  const curveBox = h('div', {});
  const drawCurve = () => {
    if (!p.curve?.length) return curveBox.replaceChildren();
    const edited = p.curve.map(([r, n]) => [r, n * scale / 100]);
    curveBox.replaceChildren(h('div', { class: 'chart' },
      h('div', { class: 'chart-head' }, h('span', { class: 'chart-title' }, t('Engine torque (Nm)')),
        h('span', { class: 'legend' }, h('span', {}, h('i', { style: 'background:#8a96a3' }), t('Original')),
          h('span', {}, h('i', { style: 'background:#ff4d2e' }), t('New')))),
      lineChart({ width: 420, height: 130, sharedY: true, xLabel: (v) => `${v} rpm`,
        series: [{ points: p.curve, color: '#8a96a3' }, { points: edited, color: '#ff4d2e' }] })));
  };
  const inputs = {};
  if (physOk) {
    inputs.mass = field(`${t('Mass')} (kg)`, p.mass, { min: 100, max: 10000, step: 1 });
    inputs.fuel = field(`${t('Fuel')} (l)`, p.fuel, { min: 1, max: 1000, step: 1 });
    inputs.limiter = field(`${t('Limiter')} (rpm)`, p.limiter, { min: 1000, max: 30000, step: 50 });
    inputs.final = field(t('Final drive'), p.final, { min: 1, max: 20, step: 0.01 });
    const turbos = p.turbos.map((tb) => field(`${t('Turbo')} ${tb.section.replace(/^TURBO_/i, '#')} (bar)`, tb.boost,
      { min: 0, max: 10, step: 0.05, 'data-section': tb.section }));
    const slider = h('input', { type: 'range', min: 50, max: 200, value: 100, step: 1, 'aria-label': t('Engine power') });
    const pct = h('b', {}, '100%');
    slider.addEventListener('input', () => { scale = Number(slider.value); pct.textContent = `${scale}%`; drawCurve(); });
    wrap.append(
      h('div', { class: 'muted small-note' }, t('Real physics (from the data folder): how the car drives in the game.'),
        p.packed ? ` ${t('This car also has data.acd; saving to the original moves it to the backup so the game uses these values.')}` : ''),
      h('div', { class: 'edit-grid' }, inputs.mass, inputs.fuel, inputs.limiter, inputs.final, ...turbos),
      h('label', { class: 'edit-field wide' }, h('span', {}, t('Engine power')), h('div', { class: 'power-row' }, slider, pct)),
      curveBox);
    inputs.turbos = turbos;
    drawCurve();
  } else {
    wrap.append(h('div', { class: 'notice warn-list small' }, p.reason === 'packed'
      ? t('This car’s physics are packed in data.acd. Unpack its data folder (for example with Content Manager) to edit how it drives; the menu specs below can still be edited.')
      : t('This car has no data folder, so only the menu specs can be edited.')));
  }
  const ui = {};
  const uiGrid = h('div', { class: 'edit-grid' }, ...[['bhp', 'Power'], ['torque', 'Torque'], ['weight', 'Weight'],
    ['topspeed', 'Top speed'], ['acceleration', '0–100'], ['pwratio', 'Power/weight']].map(([k, label]) => {
    ui[k] = h('input', { class: 'search small-input', value: info.uiSpecs[k] || '', maxlength: 40 });
    return h('label', { class: 'edit-field' }, h('span', {}, t(label)), ui[k]);
  }));
  const curvesChk = h('input', { type: 'checkbox', checked: physOk, disabled: !physOk });
  wrap.append(
    h('h5', {}, t('Shown in the game menus')),
    uiGrid,
    h('div', { class: 'edit-row' },
      physOk && h('button', { class: 'btn ghost small', onclick: () => fillFromPhysics(p, inputs, () => scale, ui) }, t('Fill in from the physics')),
      h('label', { class: 'check' }, curvesChk, h('span', {}, t('Redraw the menu power and torque charts')))),
    h('div', { class: 'edit-row end' }, h('button', {
      class: 'btn primary', onclick: () => saveSpecs(d, info, inputs, () => scale, ui, curvesChk.checked),
    }, t('Save specs…'))));
  return wrap;
}

function num(input) {
  const v = input?.querySelector('input')?.value;
  return v === '' || v == null ? null : Number(v);
}

function physicsChanges(p, inputs, scale) {
  if (!p.available) return null;
  const out = {};
  for (const k of ['mass', 'fuel', 'limiter', 'final']) {
    const v = num(inputs[k]);
    if (v != null && v !== p[k]) out[k] = v;
  }
  const turbos = (inputs.turbos || []).map((f) => ({ section: f.querySelector('input').dataset.section, boost: num(f) }))
    .filter((tb, i) => tb.boost != null && tb.boost !== p.turbos[i].boost);
  if (turbos.length) out.turbos = turbos;
  if (scale !== 100) out.scale = scale / 100;
  return Object.keys(out).length ? out : null;
}

function fillFromPhysics(p, inputs, getScale, ui) {
  const scale = getScale() / 100;
  const boosts = (inputs.turbos || []).map((f) => num(f) || 0);
  const boost = 1 + Math.max(0, ...boosts);
  const mass = num(inputs.mass) || p.mass;
  const hp = Math.max(...p.curve.map(([r, n]) => n * scale * boost * r / 7121));
  const nm = Math.max(...p.curve.map(([, n]) => n * scale * boost));
  ui.bhp.value = `${Math.round(hp)} bhp`;
  ui.torque.value = `${Math.round(nm)} Nm`;
  if (mass) { ui.weight.value = `${Math.round(mass)} kg`; ui.pwratio.value = `${(mass / hp).toFixed(2)} kg/hp`; }
}

async function saveSpecs(d, info, inputs, getScale, ui, curves) {
  const phys = physicsChanges(info.physics, inputs, getScale());
  const specs = {};
  for (const [k, el] of Object.entries(ui)) if (el.value.trim() !== (info.uiSpecs[k] || '')) specs[k] = el.value.trim();
  const uiChanges = Object.keys(specs).length || (curves && phys) ? { specs, curvesFromPhysics: curves } : null;
  if (!phys && !uiChanges) return flash(t('Nothing changed.'), false);
  const target = await chooseTarget(d.id, info.copyBlockers, t('Specs'));
  if (!target) return;
  try {
    const r = await api('/api/edit/specs', { id: d.id, target, physics: phys, ui: uiChanges });
    delete cache[d.id];
    afterSave(r, d.id);
  } catch (e) { flash(e.message, true); }
}

// ---------------------------------------------------------------- undo

function backupsList(d, info) {
  if (!info.backups.length) return null;
  return h('div', { class: 'edit-section' }, h('h4', {}, t('Undo saved changes')),
    ...info.backups.map((b) => h('div', { class: 'export-row' },
      h('div', { class: 'grow' }, h('b', {}, b.what || '—'),
        h('span', { class: 'muted' }, `${new Date(b.time * 1000).toLocaleString()} · ${b.files} ${t('Files').toLowerCase()}`)),
      h('button', {
        class: 'btn small', onclick: async () => {
          try {
            await api('/api/backups/restore', { backup: b.id });
            delete cache[d.id];
            flash(t('Restored the files from before this change.'), false);
            delete state.details[d.id];
            emit('rescan', d.id);
          } catch (e) { flash(e.message, true); }
        },
      }, t('Undo')))));
}

export function initEditor() {
  $('#saveModeNew').onchange = updateSaveDialog;
  $('#saveModeOriginal').onchange = updateSaveDialog;
  $('#saveGo').onclick = () => finishSave(true);
  $('#saveDlg').addEventListener('click', (e) => {
    if (e.target === $('#saveDlg') || e.target.closest('[data-close]')) finishSave(false);
  });
}
