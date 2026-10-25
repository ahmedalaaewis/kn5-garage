// The car library: folders, scanning, stats, filters, cards / table, selection, favourites, CSV.

import { api, fileUrl } from './api.js';
import { $, $$, debounce, downloadText, fmtBytes, h, img } from './dom.js';
import { t } from './i18n.js';
import { COLUMNS, STATUS_LABEL, csvCell, displayPath, filterCars, isBad, sortCars as sortBy } from './model.js';
import { carById, emit, on, prefs, state } from './state.js';
import { flash } from './toast.js';

export const statusLabel = (s) => t(STATUS_LABEL[s] || s);
export const showPath = (p) => displayPath(p, state.settings.home);
// The output pill shows the end of long paths (right-to-left trick); the marks keep "~\" in front
const ltr = (p) => (p ? `‎${p}‎` : p);

// ---------------------------------------------------------------- folders and scanning

export async function loadSettings() {
  state.settings = await api('/api/settings');
  $('#outPath').textContent = ltr(showPath(state.settings.out)) || '—';
  $('#outPath').title = showPath(state.settings.out);
  $('#appVersion').textContent = state.settings.version ? `v${state.settings.version}` : '';
}

export async function pick(what) {
  try {
    const r = await api('/api/pick', { what });
    if (!r.path) return;
    if (what === 'root') { state.roots = r.roots; renderRoots(); scan(); }
    else { await loadSettings(); emit('exports'); }
  } catch (e) { flash(e.message, true); }
}

export function renderRoots() {
  $('#roots').replaceChildren(...state.roots.map((r) => h('span', { class: 'root-chip', title: showPath(r) },
    h('span', { class: 'root-name' }, r.split(/[\\/]/).filter(Boolean).pop() || r),
    h('button', { class: 'root-x', title: 'Remove this folder', 'aria-label': 'Remove folder', onclick: () => removeRoot(r) }, '×'))));
  $('#scanBtn').textContent = state.roots.length > 1 ? t('Scan all ({n})', { n: state.roots.length }) : t('Scan');
}

async function saveRoots() {
  try { await api('/api/settings', { roots: state.roots }); } catch (e) { flash(e.message, true); }
}

async function addRoot(path) {
  const p = path.trim().replace(/^"(.*)"$/, '$1');  // "Copy as path" in Explorer adds quotes
  if (!p) return;
  if (!state.roots.includes(p)) state.roots.push(p);
  $('#rootInput').value = '';
  renderRoots();
  await saveRoots();
  scan();
}

async function removeRoot(path) {
  state.roots = state.roots.filter((r) => r !== path);
  renderRoots();
  await saveRoots();
  if (state.roots.length) scan();
  else { state.cars = []; state.selected = null; renderGrid(); emit('detail'); }
}

export async function scan() {
  const typed = $('#rootInput').value.trim();
  if (typed) return addRoot(typed);
  if (!state.roots.length) return pick('root');
  const btn = $('#scanBtn');
  btn.disabled = true;
  btn.replaceChildren(h('span', { class: 'spinner' }), t('Scanning'));
  const t0 = performance.now();
  try {
    const r = await api('/api/scan', { roots: state.roots });
    state.cars = r.cars;
    const ids = new Set(state.cars.map((c) => c.id));
    state.checked = new Set([...state.checked].filter((id) => ids.has(id)));
    state.details = {};
    if (!ids.has(state.selected)) state.selected = state.cars.length ? state.cars[0].id : null;
    state.scanSeconds = (performance.now() - t0) / 1000;
    renderGrid();
    emit('detail');
    emit('scanned');
  } catch (e) {
    flash(e.message, true);
  } finally {
    btn.disabled = false;
    renderRoots();
  }
}

// ---------------------------------------------------------------- filtering and sorting

export const visibleCars = () => filterCars(state.cars, state.filter, state.search, state.kind);
const sortCars = (cars) => sortBy(cars, state.sort);

export function setFilter(f) {
  state.filter = f;
  $$('#filterSeg button').forEach((x) => x.classList.toggle('on', x.dataset.f === f));
  renderGrid();
}

// ---------------------------------------------------------------- rendering

export function renderGrid() {
  const cars = sortCars(visibleCars());
  $('#empty').hidden = state.cars.length > 0;
  const vars = { shown: cars.length, total: state.cars.length, folders: state.roots.length };
  $('#listCount').textContent = !state.cars.length ? t('No folder scanned yet')
    : state.roots.length > 1 ? t('{shown} of {total} in {folders} folders', vars) : t('{shown} of {total}', vars);
  $('#scanTime').textContent = state.scanSeconds != null && state.cars.length ? t('scanned in {s}s', { s: state.scanSeconds.toFixed(1) }) : '';
  const table = state.layout === 'table';
  $('#grid').hidden = table;
  $('#table').hidden = !table || !state.cars.length;
  if (table) $('#table').replaceChildren(carTable(cars));
  else $('#grid').replaceChildren(...cars.map((c, i) => carCard(c, i)));
  $('#noMatch').hidden = !state.cars.length || cars.length > 0;
  renderStats();
  updateSelectionButtons();
}

function renderStats() {
  const el = $('#libStats');
  el.hidden = !state.cars.length;
  if (!state.cars.length) return;
  const n = (f) => state.cars.filter(f).length;
  const items = [
    ['all', 'All', state.cars.length],
    ['fav', 'Favourites', n((c) => c.favourite)],
    ['ok', 'Ready', n((c) => c.status === 'ok')],
    ['protected', 'Protected', n((c) => c.status === 'protected')],
    ['bad', 'Unreadable', n(isBad)],
    ['dup', 'Duplicates', n((c) => (c.duplicates || []).length)],
    ['issues', 'With issues', n((c) => (c.issues || []).length)],
  ];
  const kinds = [['all', 'Everything'], ['ac', 'Assetto Corsa'], ['beamng', 'BeamNG.drive'], ['model', '3D files']]
    .map(([k, label]) => [k, label, k === 'all' ? state.cars.length : n((c) => (c.kind || 'ac') === k)]);
  const kindSeg = kinds.filter(([k, , count]) => k === 'all' || count).length > 2 && h('div', { class: 'seg kind-seg' },
    ...kinds.filter(([k, , count]) => k === 'all' || count).map(([k, label, count]) => h('button', {
      class: state.kind === k ? 'on' : '', onclick: () => { state.kind = k; renderGrid(); },
    }, `${t(label)} ${count}`)));
  el.replaceChildren(
    kindSeg,
    ...items.map(([f, label, count]) => h('button', {
      class: `stat ${f}` + (state.filter === f ? ' on' : ''), title: t(label),
      onclick: () => setFilter(f),
    }, h('b', {}, count), t(label))),
    h('div', { class: 'stat-note muted' },
      `${n((c) => c.hasLod)} LOD · ${n((c) => c.hasAnimations)} ${t('Animations').toLowerCase()} · `
      + `${n((c) => c.hasExtension)} CSP · ${fmtBytes(state.cars.reduce((a, c) => a + (c.kn5Size || 0), 0))}`),
  );
}

export function flagPills(c) {
  const identical = c.identical || [];
  return [
    identical.length > 0 && h('span', { class: 'flag-pill dup', title: identical.join(', ') }, t('Identical')),
    !identical.length && (c.duplicates || []).length > 0 && h('span', { class: 'flag-pill dup', title: c.duplicates.join(', ') }, t('Duplicate')),
    (c.issues || []).length > 0 && h('span', { class: 'flag-pill issue', title: c.issues.join('\n') }, `${c.issues.length} ⚠`),
    c.kind && c.kind !== 'ac' && h('span', { class: 'flag-pill kind', title: c.format || c.kindLabel }, c.kind === 'beamng' ? 'BeamNG' : (c.tags || [])[0] || '3D'),
    c.parent && h('span', { class: 'flag-pill info', title: t('Variant of {p}', { p: c.parent }) }, 'Variant'),
    c.hasLod && h('span', { class: 'flag-pill info', title: 'Lower-detail model (Game-ready preset)' }, 'LOD'),
    c.hasAnimations && h('span', { class: 'flag-pill info', title: 'Doors, wipers…' }, 'Anim'),
    c.hasExtension && h('span', { class: 'flag-pill info', title: 'Custom Shaders Patch' }, 'CSP'),
  ];
}

export async function toggleFavourite(id) {
  const c = carById(id);
  if (!c) return;
  try {
    const r = await api('/api/carmeta', { id, favourite: !c.favourite });
    c.favourite = r.favourite;
    for (const el of $$(`[data-id="${CSS.escape(id)}"] .star`)) {
      el.classList.toggle('on', c.favourite);
      el.setAttribute('aria-pressed', String(c.favourite));
    }
    renderStats();
    emit('detail');
  } catch (e) { flash(e.message, true); }
}

function star(c) {
  return h('button', {
    class: 'star' + (c.favourite ? ' on' : ''), title: t('Favourite'), 'aria-label': t('Favourite'),
    'aria-pressed': String(!!c.favourite),
    onclick: (e) => { e.stopPropagation(); toggleFavourite(c.id); },
  }, '★');
}

function carTable(cars) {
  const head = h('tr', {}, h('th', {}, ''), h('th', {}, ''), ...COLUMNS.map(([key, label]) => h('th', {
    class: 'sortable' + (state.sort.key === key ? (state.sort.dir > 0 ? ' asc' : ' desc') : ''),
    'aria-sort': state.sort.key === key ? (state.sort.dir > 0 ? 'ascending' : 'descending') : null,
    onclick: () => {
      state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
      prefs.set('sort', state.sort);
      renderGrid();
    },
  }, t(label))), h('th', {}, ''));
  const rows = cars.map((c) => h('tr', {
    class: state.selected === c.id ? 'sel' : '', 'data-id': c.id,
    onclick: () => selectCar(c.id),
  },
    h('td', {}, h('input', {
      type: 'checkbox', class: 'row-check', checked: state.checked.has(c.id), title: 'Select', 'aria-label': 'Select ' + c.name,
      onclick: (e) => { e.stopPropagation(); toggleChecked(c.id); },
    })),
    h('td', {}, star(c)),
    h('td', { class: 'name', title: showPath(c.folder) || c.id }, c.name),
    h('td', {}, c.brand || '—'),
    h('td', {}, c.year || '—'),
    h('td', {}, c.specs?.bhp || '—'),
    h('td', {}, c.specs?.weight || '—'),
    h('td', {}, c.author || '—'),
    h('td', {}, h('span', { class: 'status inline ' + c.status }, statusLabel(c.status))),
    h('td', { class: 'num' }, c.skinCount || 0),
    h('td', { class: 'num' }, fmtBytes(c.kn5Size)),
    h('td', { class: 'flags-cell' }, h('div', { class: 'flags-wrap' }, flagPills(c))),
  ));
  return h('table', { class: 'lib-table' }, h('thead', {}, head), h('tbody', {}, ...rows));
}

function carCard(c, i) {
  const box = h('div', {
    class: 'check-box' + (state.checked.has(c.id) ? ' on' : ''), role: 'checkbox',
    'aria-checked': String(state.checked.has(c.id)), title: 'Select for batch export',
    onclick: (e) => { e.stopPropagation(); toggleChecked(c.id); },
  });
  const media = h('div', { class: 'card-img' },
    c.preview ? img(fileUrl(c.id, c.preview), 'shot')
      : h('div', { class: 'no-shot' }, c.kind && c.kind !== 'ac' ? (c.format || c.kindLabel) : t('No preview')),
    c.badge && img(fileUrl(c.id, c.badge), 'badge'),
  );
  const meta = [c.brand, c.year].filter(Boolean).join(' · ');
  return h('article', {
    class: 'card' + (state.selected === c.id ? ' sel' : ''), 'data-id': c.id, tabindex: 0,
    style: `animation-delay:${Math.min(i, 20) * 18}ms`,
    onclick: () => selectCar(c.id),
    onkeydown: (e) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === 'Enter') selectCar(c.id);
      if (e.key === ' ') { e.preventDefault(); toggleChecked(c.id); }
    },
  },
    box,
    star(c),
    h('span', { class: 'status ' + c.status }, statusLabel(c.status)),
    media,
    state.done[c.id] && h('span', { class: 'done-tag' }, '✓'),
    h('div', { class: 'card-flags' }, flagPills(c)),
    h('div', { class: 'card-body' },
      h('div', { class: 'card-name', title: c.name }, c.name),
      h('div', { class: 'card-meta' }, meta || (c.kind === 'model' ? c.format : c.id)),
      h('div', { class: 'card-meta' }, c.author ? t('by {a}', { a: c.author }) : t('Unknown author')),
      (c.userTags || []).length > 0 && h('div', { class: 'card-tags' }, c.userTags.map((x) => h('span', { class: 'tag' }, x))),
    ),
  );
}

// ---------------------------------------------------------------- selection
// (updates the existing cards / rows instead of rebuilding a big list)

export function selectCar(id) {
  state.selected = id;
  state.tab = 'overview';
  for (const el of $$('[data-id]')) el.classList.toggle('sel', el.dataset.id === id);
  emit('detail');
}

export function setChecked(ids) {
  state.checked = new Set(ids.filter((id) => carById(id)));
  markChecked();
}

function markChecked() {
  for (const el of $$('#grid [data-id]')) {
    const on = state.checked.has(el.dataset.id);
    const box = el.querySelector('.check-box');
    box.classList.toggle('on', on);
    box.setAttribute('aria-checked', String(on));
  }
  for (const el of $$('#table [data-id]')) el.querySelector('.row-check').checked = state.checked.has(el.dataset.id);
  updateSelectionButtons();
}

function toggleChecked(id) {
  state.checked.has(id) ? state.checked.delete(id) : state.checked.add(id);
  markChecked();
}

function toggleAll() {
  const vis = visibleCars();
  const all = vis.every((c) => state.checked.has(c.id));
  vis.forEach((c) => (all ? state.checked.delete(c.id) : state.checked.add(c.id)));
  markChecked();
}

function updateSelectionButtons() {
  const n = state.checked.size;
  const btn = $('#convertSel');
  btn.disabled = n === 0;
  btn.textContent = n ? t('Export selected ({n})', { n }) : t('Export selected');
  const visible = visibleCars();
  $('#selectAll').textContent = visible.length && visible.every((c) => state.checked.has(c.id)) ? t('Clear selection') : t('Select all');
  const cmp = $('#compareSel');
  cmp.disabled = n < 2 || n > 6;
  cmp.textContent = n >= 2 ? t('Compare ({n})', { n }) : t('Compare');
  cmp.title = n > 6 ? 'Compare up to 6 cars' : 'Select 2 to 6 cars to compare';
}

// ---------------------------------------------------------------- CSV

const CSV_FIELDS = [
  ['Name', (c) => c.name], ['Brand', (c) => c.brand], ['Year', (c) => c.year], ['Class', (c) => c.carClass],
  ['Author', (c) => c.author], ['Version', (c) => c.version], ['Status', (c) => STATUS_LABEL[c.status] || c.status],
  ['Favourite', (c) => (c.favourite ? 'yes' : '')], ['My tags', (c) => (c.userTags || []).join('; ')],
  ['Power', (c) => c.specs?.bhp], ['Torque', (c) => c.specs?.torque], ['Weight', (c) => c.specs?.weight],
  ['Top speed', (c) => c.specs?.topspeed], ['0-100', (c) => c.specs?.acceleration], ['Skins', (c) => c.skinCount],
  ['Model MB', (c) => (c.kn5Size / 1048576).toFixed(1)], ['LOD', (c) => (c.hasLod ? 'yes' : '')],
  ['Animations', (c) => (c.hasAnimations ? 'yes' : '')], ['CSP', (c) => (c.hasExtension ? 'yes' : '')],
  ['Variant of', (c) => c.parent], ['Duplicates', (c) => (c.duplicates || []).join(' ')],
  ['Issues', (c) => (c.issues || []).join('; ')], ['Folder name', (c) => c.id],
];

function exportCsv() {
  const cars = sortCars(visibleCars());
  const lines = [CSV_FIELDS.map(([k]) => k).join(','),
    ...cars.map((c) => CSV_FIELDS.map(([, f]) => csvCell(f(c))).join(','))];
  downloadText('kn5-garage-library.csv', '﻿' + lines.join('\r\n'), 'text/csv');  // BOM: Excel reads UTF-8
}

// ---------------------------------------------------------------- wiring

export function setLayout(layout) {
  state.layout = layout === 'table' ? 'table' : 'cards';
  prefs.set('layout', state.layout);
  $$('#layoutSeg button').forEach((x) => x.classList.toggle('on', x.dataset.l === state.layout));
  renderGrid();
}

export function initLibrary() {
  state.layout = prefs.get('layout', 'cards') === 'table' ? 'table' : 'cards';
  const sort = prefs.get('sort', null);
  if (sort && COLUMNS.some((c) => c[0] === sort.key) && (sort.dir === 1 || sort.dir === -1)) state.sort = sort;
  $$('#layoutSeg button').forEach((x) => x.classList.toggle('on', x.dataset.l === state.layout));

  $('#rootBrowse').onclick = () => pick('root');
  $('#emptyBrowse').onclick = () => pick('root');
  $('#scanBtn').onclick = scan;
  $('#rootInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') addRoot(e.target.value); });
  $('#outChange').onclick = () => pick('out');
  $('#outPill').onclick = () => pick('out');
  const search = debounce(() => renderGrid(), 120);
  $('#search').addEventListener('input', (e) => { state.search = e.target.value; search(); });
  $('#filterSeg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) setFilter(b.dataset.f);
  });
  $('#layoutSeg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) setLayout(b.dataset.l);
  });
  $('#selectAll').onclick = toggleAll;
  $('#csvBtn').onclick = exportCsv;
  // after an edit is saved: read the folders again and show the edited (or new) car
  on('rescan', async (id) => {
    await scan();
    if (id && carById(id)) { selectCar(id); state.tab = 'edit'; emit('detail'); }
  });
}

export async function startLibrary() {
  await loadSettings();
  state.roots = [...(state.settings.roots || [])];
  renderRoots();
  if (state.roots.length) await scan();
}
