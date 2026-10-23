// Library extras: disk space, the skin gallery and the export history.

import { api, fileUrl } from './api.js';
import { $, debounce, fmtBytes, h, img } from './dom.js';
import { openExportDialog } from './exporter.js';
import { t } from './i18n.js';
import { selectCar } from './library.js';
import { filterSkins, wastedBytes } from './model.js';
import { carById, emit, on, state } from './state.js';
import { flash } from './toast.js';

// ---------------------------------------------------------------- disk space

export async function openSpace() {
  const body = $('#spaceBody');
  body.replaceChildren(h('div', { class: 'muted' }, h('span', { class: 'spinner' }), ' …'));
  $('#spaceDlg').hidden = false;
  let r;
  try { r = await api('/api/space'); } catch (e) { body.replaceChildren(h('div', {}, e.message)); return; }
  const max = Math.max(...r.cars.map((c) => c.total), 1);
  const sizes = Object.fromEntries(r.cars.map((c) => [c.id, c.total]));
  const wasted = wastedBytes(state.cars, sizes);
  const bar = (c) => h('div', { class: 'space-bar', title: `${t('Model')} ${fmtBytes(c.model)} · ${t('Skins')} ${fmtBytes(c.skins)} · ${t('Other')} ${fmtBytes(c.other)}` },
    h('i', { class: 'm', style: `width:${(c.model / max) * 100}%` }),
    h('i', { class: 's', style: `width:${(c.skins / max) * 100}%` }),
    h('i', { class: 'o', style: `width:${(c.other / max) * 100}%` }));
  body.replaceChildren(
    h('div', { class: 'space-head' },
      h('div', {}, h('b', {}, fmtBytes(r.total)), ' ', t('Total')),
      h('span', { class: 'legend' }, h('span', {}, h('i', { class: 'm' }), t('Model')),
        h('span', {}, h('i', { class: 's' }), t('Skins')), h('span', {}, h('i', { class: 'o' }), t('Other'))),
      wasted > 0 && h('div', { class: 'notice warn-list small' }, t('Identical copies could free {size}', { size: fmtBytes(wasted) }))),
    h('div', { class: 'space-list' }, r.cars.map((c) => {
      const car = carById(c.id);
      return h('button', { class: 'space-row', onclick: () => { $('#spaceDlg').hidden = true; selectCar(c.id); } },
        h('span', { class: 'space-name', title: c.id }, car?.name || c.id,
          (car?.identical || []).length > 0 && h('span', { class: 'flag-pill dup' }, t('Identical'))),
        bar(c), h('span', { class: 'space-size' }, fmtBytes(c.total)));
    })));
}

// ---------------------------------------------------------------- skin gallery

let skins = [];

export async function openSkins(search = '') {
  $('#skinsDlg').hidden = false;
  $('#skinSearch').value = search;
  const grid = $('#skinGallery');
  grid.replaceChildren(h('div', { class: 'muted' }, h('span', { class: 'spinner' }), ' …'));
  try { skins = (await api('/api/skins')).skins; } catch (e) { grid.replaceChildren(h('div', {}, e.message)); return; }
  renderSkins();
  $('#skinSearch').focus();
}

function renderSkins() {
  const shown = filterSkins(skins, $('#skinSearch').value, (id) => carById(id)?.name || id);
  $('#skinGallery').replaceChildren(...[...shown.slice(0, 600).map((s) => h('button', {
    class: 'skin gallery-skin', title: `${carById(s.car)?.name || s.car} · ${s.name}`,
    onclick: () => {
      state.skin[s.car] = s.id;
      $('#skinsDlg').hidden = true;
      selectCar(s.car);
    },
  },
    h('div', { class: 'skin-img' },
      s.preview && img(fileUrl(s.car, s.preview)),
      s.number && h('span', { class: 'skin-num' }, `#${s.number}`)),
    h('div', { class: 'skin-body' },
      h('div', { class: 'skin-name' }, s.name),
      h('div', { class: 'skin-driver' }, [s.driver, s.team].filter(Boolean).join(' · ') || '—'),
      h('div', { class: 'skin-car' }, carById(s.car)?.name || s.car)),
  )), shown.length > 600 && h('div', { class: 'muted' }, `+${shown.length - 600}`)].filter(Boolean));
}

// ---------------------------------------------------------------- history

export async function openHistory() {
  $('#historyDrawer').hidden = false;
  await renderHistory();
}

async function renderHistory() {
  if ($('#historyDrawer').hidden) return;
  let items;
  try { items = (await api('/api/history')).items; } catch (e) { flash(e.message, true); return; }
  const byFolder = Object.fromEntries(state.cars.map((c) => [(c.folder || '').toLowerCase(), c]));
  $('#historyList').replaceChildren(...(items.length ? items.map((i) => {
    const car = byFolder[(i.folder || '').toLowerCase()];
    const o = i.options || {};
    return h('div', { class: 'export-row history-row' + (i.ok ? '' : ' failed') },
      h('div', { class: 'grow' },
        h('b', { title: i.file || i.error }, car?.name || i.id),
        h('span', { class: 'muted' }, [o.preset || 'render', o.paint, o.textureSize && `${o.textureSize}px`,
          o.lightsOn && t('Lights'), new Date(i.time * 1000).toLocaleString()].filter(Boolean).join(' · ')),
        !i.ok && h('span', { class: 'err-text' }, i.error)),
      h('button', {
        class: 'btn small', disabled: !car, title: car ? null : t('Not scanned'),
        onclick: () => {
          if (o.skin) state.skin[car.id] = o.skin;
          if (o.paintMaterials) state.paintNames[car.id] = o.paintMaterials;
          $('#historyDrawer').hidden = true;
          selectCar(car.id);
          openExportDialog([car.id], o);
        },
      }, t('Again')));
  }) : [h('p', { class: 'muted' }, t('Nothing exported yet.'))]));
}

export function initExtras() {
  $('#spaceBtn').onclick = openSpace;
  $('#skinsBtn').onclick = () => openSkins();
  $('#skinSearch').addEventListener('input', debounce(renderSkins, 120));
  $('#historyBtn').onclick = openHistory;
  $('#historyClose').onclick = () => { $('#historyDrawer').hidden = true; };
  $('#historyClear').onclick = async () => {
    try { await api('/api/history/clear', {}); renderHistory(); } catch (e) { flash(e.message, true); }
  };
  on('history', renderHistory);
  on('scanned', () => emit('history'));
}
