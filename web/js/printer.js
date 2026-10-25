// 3D printing: any car, vehicle or figure as a printable STL / 3MF, at a real height, repaired
// (holes closed, triangles facing out) and cut into pieces that fit the printer.

import { api } from './api.js';
import { $, h } from './dom.js';
import { t } from './i18n.js';
import { displayPath } from './model.js';
import { carById, prefs, state } from './state.js';
import { flash } from './toast.js';

let current = null;

export function openPrint(id) {
  current = id;
  const c = carById(id);
  const height = prefs.get('printHeight', 150);
  $('#printTitle').textContent = `${t('3D print')}: ${c?.name || id}`;
  $('#printBody').replaceChildren(
    h('div', { class: 'muted' }, t('Makes a file your slicer can open (Cura, PrusaSlicer, Bambu Studio…): in millimetres, standing on the bed.')),
    h('div', { class: 'edit-grid' },
      h('label', { class: 'edit-field' }, h('span', {}, t('Height (mm)')),
        h('input', { class: 'search small-input', id: 'prHeight', type: 'number', min: 1, max: 2000, value: height })),
      h('label', { class: 'edit-field' }, h('span', {}, t('File')),
        h('select', { class: 'select small', id: 'prFormat' },
          h('option', { value: 'stl' }, 'STL'), h('option', { value: '3mf' }, '3MF'))),
      h('label', { class: 'edit-field wide' }, h('span', {}, t('Cut into pieces at these heights (mm, optional)')),
        h('input', { class: 'search small-input', id: 'prCuts', placeholder: t('for example 50, 100') }))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', id: 'prRepair', checked: true }),
      h('span', {}, t('Repair: close holes and turn every triangle outwards'))),
    h('label', { class: 'edit-field' }, h('span', {}, t('Wall thickness for thin surfaces (mm, optional)')),
      h('input', { class: 'search small-input', id: 'prThick', type: 'number', min: 0.2, max: 50, step: 0.1,
        placeholder: t('for example 2 for a car body') })),
    h('div', { id: 'prResult' }),
  );
  $('#printDlg').hidden = false;
}

async function run() {
  const height = Number($('#prHeight').value);
  if (!(height >= 1 && height <= 2000)) return flash(t('The height must be 1-2000 mm.'), true);
  const cuts = $('#prCuts').value.split(/[,;\s]+/).filter(Boolean).map(Number);
  if (cuts.some((c) => !(c > 0 && c < height))) return flash(t('Cut heights must be between 0 and the height.'), true);
  prefs.set('printHeight', height);
  const btn = $('#printGo');
  btn.disabled = true;
  btn.textContent = t('Working…');
  try {
    const thick = Number($('#prThick').value) || null;
    if (thick != null && !(thick >= 0.2 && thick <= 50)) return flash(t('The wall thickness must be 0.2-50 mm.'), true);
    const r = await api('/api/print', { id: current, heightMm: height, cuts, format: $('#prFormat').value,
      repair: $('#prRepair').checked, ...(thick ? { thicknessMm: thick } : {}) });
    const rep = r.report;
    $('#prResult').replaceChildren(h('div', { class: 'notice ' + (rep.watertight && !rep.flat ? 'ok-note' : 'warn-list small') },
      h('div', {},
        h('b', {}, r.files.join(', ')),
        h('div', {}, `${rep.size.join(' × ')} mm · ${rep.volume} cm³ · ${t('{n} triangles', { n: rep.triangles.toLocaleString() })}`),
        h('div', {}, rep.flat ? t('This model has no thickness (single surfaces), so there is nothing to fill: give it thickness in a 3D program first.')
          : rep.watertight ? t('Closed and ready to slice.')
            : t('Some holes could not be closed; most slicers can still fix them.')),
        rep.holesClosed ? h('div', { class: 'muted' }, t('{n} holes closed', { n: rep.holesClosed })) : null,
        h('div', { class: 'muted path' }, displayPath(r.folder, state.settings.home)))));
    flash(t('Saved {file}', { file: r.files[0] }), false, { label: t('Show folder'), run: () => api('/api/open', { what: 'out' }) });
  } catch (e) { flash(e.message, true); }
  finally { btn.disabled = false; btn.textContent = t('Make the file'); }
}

export function initPrint() {
  $('#printGo').onclick = run;
}
