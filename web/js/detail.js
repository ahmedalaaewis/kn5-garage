// The detail panel on the right: one car's info, tabs, and its export / preview actions.

import { api, fileUrl, q } from './api.js';
import { openInBlender } from './blender.js';
import { lineChart, peak } from './charts.js';
import { $, ICON, fmtBytes, h, img } from './dom.js';
import { openExportDialog } from './exporter.js';
import { t } from './i18n.js';
import { flagPills, selectCar, showPath, statusLabel, toggleFavourite } from './library.js';
import { isBad, safeUrl } from './model.js';
import { carById, emit, on, state } from './state.js';
import { flash } from './toast.js';
import { openPreview, openViewer } from './viewer-ui.js';

export async function loadDetails(id) {
  if (!state.details[id]) state.details[id] = await api(`/api/car?${q({ id })}`);
  return state.details[id];
}

export async function renderDetail() {
  const pane = $('#detail');
  const id = state.selected;
  if (!id) { pane.replaceChildren(h('div', { class: 'detail-empty muted' }, t('Select a car to see its details'))); return; }
  if (!state.details[id]) {
    pane.replaceChildren(h('div', { class: 'detail-empty muted' }, h('span', { class: 'spinner' })));
    try { await loadDetails(id); }
    catch (e) { pane.replaceChildren(h('div', { class: 'detail-empty' }, e.message)); return; }
    if (state.selected !== id) return;
  }
  const d = state.details[id];
  const c = carById(id) || {};
  if (!(id in state.skin)) state.skin[id] = d.skins.length ? d.skins[0].id : null;
  const skin = d.skins.find((x) => x.id === state.skin[id]);
  const heroImg = skin?.preview || d.preview;

  const hero = h('div', { class: 'hero' },
    heroImg && img(fileUrl(id, heroImg), 'shot'),
    h('button', {
      class: 'star big' + (c.favourite ? ' on' : ''), title: t('Favourite'), 'aria-label': t('Favourite'),
      'aria-pressed': String(!!c.favourite), onclick: () => toggleFavourite(id),
    }, '★'),
    h('div', { class: 'hero-text' },
      h('div', { class: 'hero-brand' }, d.badge && img(fileUrl(id, d.badge)), d.brand || t('Unknown brand')),
      h('h2', {}, d.name),
    ),
  );

  const chips = h('div', { class: 'chips' },
    h('span', { class: 'chip strong status-chip' }, statusLabel(d.status)),
    d.year && h('span', { class: 'chip' }, d.year),
    d.country && h('span', { class: 'chip' }, d.country),
    d.carClass && h('span', { class: 'chip' }, d.carClass),
    ...(d.tags || []).slice(0, 5).map((x) => h('span', { class: 'chip' }, x)),
  );

  const url = safeUrl(d.url);
  const author = h('div', { class: 'author' },
    h('div', { class: 'avatar' }, (d.author || '?').trim().charAt(0).toUpperCase()),
    h('div', {},
      h('div', { class: 'author-label' }, t('Author')),
      h('div', { class: 'author-name' }, d.author || t('Not listed')),
      url && h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, url.replace(/^https?:\/\//, '')),
    ),
    h('div', { class: 'ver' }, d.version ? `v${String(d.version).replace(/^v/i, '')}` : ''),
  );

  let notice = null;
  if (d.status === 'protected') {
    notice = h('div', { class: 'notice protected' }, ICON.lock(),
      h('div', {}, h('b', {}, t('Protected')), d.statusText));
  } else if (d.status !== 'ok') {
    notice = h('div', { class: 'notice bad' }, ICON.alert(),
      h('div', {}, h('b', {}, statusLabel(d.status)), d.statusText));
  }

  const tabs = h('div', { class: 'tabs', role: 'tablist' },
    ...[['overview', t('Overview')], ['skins', `${t('Skins')} (${d.skins.length})`], ['files', t('Files')]].map(([k, label]) =>
      h('button', { class: state.tab === k ? 'on' : '', role: 'tab', 'aria-selected': String(state.tab === k),
        onclick: () => { state.tab = k; renderDetail(); } }, label)),
  );
  const panel = state.tab === 'skins' ? skinsTab(d) : state.tab === 'files' ? filesTab(d) : overviewTab(d);

  pane.replaceChildren(hero,
    h('div', { class: 'detail-body' }, chips, h('div', { class: 'card-flags inline' }, flagPills(c)), author,
      variantNote(d), notice, problemsNotice(id), tagEditor(c),
      h('div', { class: 'readonly' }, ICON.shield(), t('Read-only: this car folder is never modified')),
      tabs, panel),
    actionBar(d, skin),
  );
}

function variantNote(d) {
  if (!d.parent) return null;
  const parent = state.cars.find((x) => x.id.toLowerCase() === d.parent.toLowerCase());
  return h('div', { class: 'muted variant' }, t('Variant of {p}', { p: '' }),
    parent ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); selectCar(parent.id); } }, parent.name)
      : h('code', {}, d.parent));
}

function tagEditor(c) {
  if (!c.id) return null;
  const tags = c.userTags || [];
  const save = async (next) => {
    try {
      const r = await api('/api/carmeta', { id: c.id, tags: next });
      c.userTags = r.tags;
      state.settings.tags = r.allTags;
      emit('grid');
      renderDetail();
    } catch (e) { flash(e.message, true); }
  };
  const input = h('input', {
    class: 'tag-input', list: 'tagList', placeholder: t('Add a tag and press Enter'), maxlength: 40,
    'aria-label': t('Your tags'),
    onkeydown: (e) => { if (e.key === 'Enter' && e.target.value.trim()) save([...tags, e.target.value.trim()]); },
  });
  return h('div', { class: 'tag-editor' },
    h('span', { class: 'author-label' }, t('Your tags')),
    ...tags.map((x) => h('span', { class: 'tag' }, x,
      h('button', { class: 'tag-x', 'aria-label': 'Remove tag', onclick: () => save(tags.filter((y) => y !== x)) }, '×'))),
    input,
    h('datalist', { id: 'tagList' }, (state.settings.tags || []).filter((x) => !tags.includes(x)).map((x) => h('option', { value: x }))),
  );
}

function problemsNotice(id) {
  const c = carById(id) || {};
  const issues = c.issues || [];
  const dups = c.duplicates || [];
  const identical = new Set(c.identical || []);
  if (!issues.length && !dups.length) return null;
  const link = (x) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); selectCar(x); } }, x);
  return h('div', { class: 'notice warn-list' }, ICON.alert(),
    h('div', {},
      issues.length > 0 && h('div', {}, h('b', {}, t('Issues')), issues.join(' · ')),
      identical.size > 0 && h('div', {}, h('b', {}, t('Identical copies (same model file)')),
        [...identical].map((x, i) => [i ? ', ' : '', link(x)])),
      dups.some((x) => !identical.has(x)) && h('div', {}, h('b', {}, t('Same name')),
        dups.filter((x) => !identical.has(x)).map((x, i) => [i ? ', ' : '', link(x)]))));
}

function overviewTab(d) {
  const S = d.specs || {};
  const spec = (label, v) => h('div', { class: 'spec' },
    h('div', { class: 'spec-label' }, t(label)),
    h('div', { class: 'spec-value' + (v ? '' : ' na') }, v || '—'));
  const cd = d.carData || {};
  const data = [
    cd.mass && [t('Mass'), `${cd.mass} kg`], cd.drive && [t('Drive'), cd.drive], cd.gears && [t('Gears'), cd.gears],
    cd.limiter && [t('Limiter'), `${cd.limiter} rpm`], cd.fuel && [t('Fuel'), `${cd.fuel} l`], cd.turbo && [t('Turbo'), t('Yes')],
  ].filter(Boolean);
  return h('div', { class: 'tab-panel' },
    h('div', { class: 'specs' },
      spec('Power', S.bhp), spec('Torque', S.torque), spec('Weight', S.weight),
      spec('Top speed', S.topspeed), spec('0–100', S.acceleration), spec('Power/weight', S.pwratio)),
    data.length > 0 && h('div', { class: 'sub-block' }, h('div', { class: 'author-label' }, t('From the data folder')),
      h('div', { class: 'chips' }, data.map(([k, v]) => h('span', { class: 'chip' }, h('b', {}, k + ' '), v)))),
    (d.csp || []).length > 0 && h('div', { class: 'sub-block' }, h('div', { class: 'author-label' }, 'Custom Shaders Patch'),
      h('div', { class: 'chips' }, d.csp.map((x) => h('span', { class: 'chip csp' }, x)))),
    (d.powerCurve.length || d.torqueCurve.length) ? curveChart(d.powerCurve, d.torqueCurve) : null,
    d.description && h('div', { class: 'desc' }, d.description),
  );
}

function curveChart(power, torque) {
  const pp = peak(power), tp = peak(torque);
  return h('div', { class: 'chart' },
    h('div', { class: 'chart-head' },
      h('span', { class: 'chart-title' }, t('Power & torque')),
      h('span', { class: 'legend' },
        pp && h('span', {}, h('i', { style: 'background:#ff4d2e' }), `${Math.round(pp[1])} hp @ ${Math.round(pp[0])}`),
        tp && h('span', {}, h('i', { style: 'background:#4da3ff' }), `${Math.round(tp[1])} Nm @ ${Math.round(tp[0])}`)),
    ),
    lineChart({ width: 420, height: 150, xLabel: (v) => `${v} rpm`,
      series: [{ points: torque, color: '#4da3ff' }, { points: power, color: '#ff4d2e' }] }),
  );
}

function skinsTab(d) {
  if (!d.skins.length) return h('div', { class: 'tab-panel muted' }, 'This car has no skins folder; the textures inside the model are used.');
  return h('div', { class: 'tab-panel' },
    h('div', { class: 'muted' }, t('Pick the skin to export with.')),
    h('div', { class: 'skins' }, ...d.skins.map((x) => h('button', {
      class: 'skin' + (state.skin[d.id] === x.id ? ' on' : ''),
      onclick: () => { state.skin[d.id] = x.id; renderDetail(); },
    },
      h('div', { class: 'skin-img' },
        x.preview && img(fileUrl(d.id, x.preview)),
        x.number && h('span', { class: 'skin-num' }, `#${x.number}`),
        x.livery && img(fileUrl(d.id, x.livery), 'livery')),
      h('div', { class: 'skin-body' },
        h('div', { class: 'skin-name', title: x.name }, x.name),
        h('div', { class: 'skin-driver' }, [x.driver, x.team].filter(Boolean).join(' · ') || x.id)),
    ))),
  );
}

function filesTab(d) {
  const yes = (v) => t(v ? 'Yes' : 'No');
  const root = carById(d.id)?.root;
  return h('div', { class: 'tab-panel' },
    h('table', { class: 'files' }, h('tbody', {}, ...d.files.map((f) => h('tr', {},
      h('td', {}, f.name),
      h('td', {}, h('span', { class: 'role' + (f.role === 'main model' ? ' main' : '') }, f.role)),
      h('td', {}, fmtBytes(f.size)))))),
    h('div', { class: 'flags' },
      h('div', { class: 'flag' }, h('b', {}, fmtBytes(d.folderSize)), t('Folder size')),
      h('div', { class: 'flag' }, h('b', {}, d.animations), t('Animations')),
      h('div', { class: 'flag' }, h('b', {}, d.hasDataAcd ? 'Packed' : d.hasDataFolder ? 'Folder' : 'None'), t('Car data')),
      h('div', { class: 'flag' }, h('b', {}, yes(d.hasExtension)), t('CSP extension')),
    ),
    h('div', { class: 'path' }, showPath(d.path)),
    state.roots.length > 1 && root && h('div', { class: 'muted' }, 'Found in: ', h('span', { class: 'path' }, showPath(root))),
    h('div', {}, h('button', {
      class: 'btn ghost small',
      onclick: () => api('/api/open', { what: 'car', id: d.id }).catch((e) => flash(e.message, true)),
    }, t('Open car folder'))),
  );
}

function reportList(items) {
  return h('ul', { class: 'report' }, ...items.map((i) => h('li', { class: 'rep ' + i.level }, i.text)));
}

function actionBar(d, skin) {
  const job = state.carJobs[d.id];
  const done = state.done[d.id];
  const running = job && !job.finished;
  const canExport = !isBad(d);
  const children = [];
  if (running) {
    children.push(h('div', { class: 'bar' }, h('div', { id: 'carBar', style: `width:${Math.round((job.fraction || 0) * 100)}%` })));
    children.push(h('div', { class: 'action-sub', id: 'carMsg' }, job.message || '…'));
  } else if (job?.result && !job.result.ok) {
    children.push(h('div', { class: 'result err' }, h('div', { class: 'result-text' },
      h('b', {}, t(job.result.cancelled ? 'Export cancelled' : 'Export failed')), job.result.error)));
  } else if (done) {
    const glb = done.file.endsWith('.glb');
    children.push(h('div', { class: 'result' },
      h('div', { class: 'result-text' }, h('b', { title: done.file }, done.file),
        `${fmtBytes(done.size)} · ${done.seconds}s${done.protected ? ' · shape only (protected)' : ''}`),
      glb && h('button', { class: 'btn small', onclick: () => openViewer(done.file, d.name, d.id) }, t('View 3D')),
      glb && h('button', { class: 'btn ghost small', onclick: () => openInBlender(done.file) }, 'Blender'),
      h('button', { class: 'btn ghost small', onclick: () => api('/api/open', { what: 'out' }) }, t('Folder'))));
    if (done.report?.length) children.push(reportList(done.report));
  }
  children.push(h('div', { class: 'action-row' },
    h('button', {
      class: 'btn big', disabled: !canExport, title: 'A quick, light 3D preview with the chosen skin',
      onclick: () => openPreview(d.id),
    }, t('Preview 3D')),
    h('button', {
      class: 'btn primary big', disabled: running || !canExport,
      onclick: () => openExportDialog([d.id]),
    }, t(running ? 'Exporting…' : done ? 'Export again…' : 'Export…'))));
  const paint = state.paintNames[d.id];
  children.push(h('div', { class: 'action-sub' },
    canExport ? (skin ? t('Skin: {s}', { s: skin.name }) : t('Using the textures inside the model')) : t('This car can’t be exported'),
    paint?.length ? ` · ${t('Paint parts')}: ${paint.length}` : ''));
  return h('div', { class: 'action' }, ...children);
}

// Live progress for the selected car while its export runs
function jobProgress(id) {
  if (id !== state.selected) return;
  const cj = state.carJobs[id];
  const bar = $('#carBar');
  if (bar && cj && !cj.finished) {
    bar.style.width = `${Math.round(cj.fraction * 100)}%`;
    $('#carMsg').textContent = cj.message;
  } else renderDetail();
}

export function initDetail() {
  on('detail', renderDetail);
  on('job-progress', jobProgress);
}
