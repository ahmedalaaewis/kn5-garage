// KN5 Garage front end: starts each part of the page, the language, deep links and global keys.
// The parts live in web/js/ (library, detail, compare, exporter, extras, viewer-ui, blender);
// they talk through the event bus in state.js rather than importing each other in circles.

import { api } from './js/api.js';
import { initBlender } from './js/blender.js';
import { initBones, openBones } from './js/bones.js';
import { initCompare, openCompare } from './js/compare.js';
import { initDetail } from './js/detail.js';
import { initEditor } from './js/editor.js';
import { initImport, openImport } from './js/importer.js';
import { initLivery, openLivery } from './js/livery.js';
import { initParts, openParts } from './js/parts.js';
import { initPrint, openPrint } from './js/printer.js';
import { $, $$, h } from './js/dom.js';
import { initExporter, openExportDialog, refreshExports } from './js/exporter.js';
import { initExtras, openHistory, openSkins, openSpace } from './js/extras.js';
import { LANGUAGES, preferredLanguage, setLanguage, translatePage } from './js/i18n.js';
import { initLibrary, renderGrid, renderRoots, selectCar, setChecked, setFilter, setLayout, startLibrary } from './js/library.js';
import { emit, on, prefs, state } from './js/state.js';
import { flash, initToast } from './js/toast.js';
import {
  closeParts, closeViewer, initViewer, openPreview, openViewer, partsOpen, setLights, setMode, setPaintColour,
  setSteer, setStudio, viewerOpen,
} from './js/viewer-ui.js';

function closeTopLayer() {
  const modal = $$('.modal').find((m) => !m.hidden);
  if (modal) modal.hidden = true;
  else if (partsOpen()) closeParts();
  else if (viewerOpen()) closeViewer();
  else { $('#exports').hidden = true; $('#historyDrawer').hidden = true; }
}

function initGlobal() {
  $$('.modal').forEach((m) => m.addEventListener('click', (e) => {
    if (e.target === m || e.target.closest('[data-close]')) m.hidden = true;
  }));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeTopLayer();
    const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName);
    if (e.key === '/' && !typing) { e.preventDefault(); $('#search').focus(); }
  });
  on('grid', renderGrid);
}

// ---------------------------------------------------------------- language

function applyLanguage(code) {
  setLanguage(code);
  $('#langSel').value = code;
  translatePage();
  renderRoots();
  renderGrid();
  emit('detail');
  refreshExports();
}

function initLanguage() {
  const sel = $('#langSel');
  sel.replaceChildren(...Object.entries(LANGUAGES).map(([code, name]) => h('option', { value: code }, name)));
  sel.onchange = () => {
    prefs.set('language', sel.value);
    api('/api/settings', { language: sel.value }).catch(() => {});
    applyLanguage(sel.value);
  };
  const code = prefs.get('language', null) || state.settings.language || preferredLanguage();
  setLanguage(code);
  sel.value = code in LANGUAGES ? code : 'en';
  translatePage();
}

// ---------------------------------------------------------------- deep links
// #car=<id>&tab=skins&layout=table&filter=fav&search=…&select=a,b&open=compare|space|skins|history|exports|export
//  |import|parts|livery|print &bones=1 &preset=game&viewer=preview|<file.glb>&studio=day&mode=texwire&paint=%23c8102e&paintMode=solid
//  &steer=20&lights=1&spin=0
// Handy for bookmarks, and used by tools/screenshots.py.

async function applyDeepLink() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (![...p.keys()].length) return;
  if (p.get('lang') in LANGUAGES) applyLanguage(p.get('lang'));
  if (p.get('layout')) setLayout(p.get('layout'));
  if (p.get('filter')) setFilter(p.get('filter'));
  if (p.get('search')) { state.search = p.get('search'); $('#search').value = state.search; renderGrid(); }
  if (p.get('select')) setChecked(p.get('select').split(','));
  if (p.get('car') && state.cars.some((c) => c.id === p.get('car'))) {
    if (p.get('skin')) state.skin[p.get('car')] = p.get('skin');
    selectCar(p.get('car'));
    if (p.get('tab')) { state.tab = p.get('tab'); emit('detail'); }
  }
  if (p.get('studio')) setStudio(p.get('studio'));
  if (p.get('spin') === '0') $('#spinChk').checked = false;
  const open = p.get('open');
  if (open === 'compare') await openCompare();
  if (open === 'space') await openSpace();
  if (open === 'skins') await openSkins(p.get('skinSearch') || '');
  if (open === 'history') await openHistory();
  if (open === 'exports') { await refreshExports(); $('#exports').hidden = false; }
  if (open === 'import') openImport();
  if (open === 'print' && state.selected) openPrint(state.selected);
  if (open === 'parts' && state.selected) await openParts(state.selected);
  if (open === 'livery' && state.selected) await openLivery(state.selected);
  if (open === 'export' && state.selected) {
    if (p.get('preset')) state.preset = p.get('preset');
    openExportDialog([state.selected]);
  }
  const v = p.get('viewer');
  if (v) {
    if (v === 'preview' && state.selected) await openPreview(state.selected);
    else if (v.endsWith('.glb')) await openViewer(v, v.replace(/\.glb$/, ''), state.selected);
    if (p.get('mode')) setMode(p.get('mode'));
    if (p.get('paint')) setPaintColour(p.get('paint'), p.get('paintMode') || 'tint');
    if (p.get('steer')) setSteer(Number(p.get('steer')));
    if (p.get('lights') === '1') setLights(true);
    if (p.get('bones') === '1') openBones();
  }
  document.body.dataset.ready = '1';  // tells tools/screenshots.py the page is set up
}

// ---------------------------------------------------------------- start

initToast();
initGlobal();
initLibrary();
initDetail();
initEditor();
initLivery();
initParts();
initImport();
initPrint();
initCompare();
initExporter();
initExtras();
initViewer();
initBones();
(async () => {
  try {
    await Promise.all([startLibrary(), initBlender()]);
    initLanguage();
    renderRoots();
    renderGrid();
    emit('detail');
    await applyDeepLink();
  } catch (e) { flash(e.message, true); }
})();
