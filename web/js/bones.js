// Characters in the 3D viewer: show the skeleton, pick a bone (in the list or by clicking near
// it), and pose it with three sliders. Poses are for looking and pictures (Snapshot, Turntable);
// the file itself isn't changed.

import { $, h } from './dom.js';
import { t } from './i18n.js';
import { on } from './state.js';
import { viewer } from './viewer-ui.js';

const MAX_ROWS = 300;
let s = null;  // {selected, filter, pose: {bone index: [x, y, z]}}

export function openBones() {
  s = s || { selected: null, filter: '', pose: {} };
  $('#partsPanel').hidden = true;
  $('#liveryPanel').hidden = true;
  $('#bonesPanel').hidden = false;
  $('#bonesBtn').classList.add('on');
  $('#bonesBtn').setAttribute('aria-pressed', 'true');
  viewer.showBones(true);
  render();
}

export function closeBones() {
  $('#bonesPanel').hidden = true;
  $('#bonesBtn').classList.remove('on');
  $('#bonesBtn').setAttribute('aria-pressed', 'false');
  viewer.showBones(false);
  viewer.markBone(null);
}

function slider(axis, k) {
  const value = s.pose[s.selected]?.[k] ?? 0;
  const out = h('b', { class: 'bone-val' }, `${value}°`);
  const input = h('input', { type: 'range', min: -180, max: 180, step: 1, value, 'aria-label': `${axis}` });
  input.addEventListener('input', () => {
    const pose = s.pose[s.selected] || [0, 0, 0];
    pose[k] = Number(input.value);
    s.pose[s.selected] = pose;
    out.textContent = `${pose[k]}°`;
    viewer.poseBone(s.selected, pose);
    $('#boneReset').disabled = false;
  });
  input.addEventListener('dblclick', () => { input.value = 0; input.dispatchEvent(new Event('input')); });
  return h('label', { class: 'bone-slider' }, h('span', {}, axis), input, out);
}

function choose(i) {
  s.selected = i;
  viewer.markBone(i);
  render();
  document.querySelector('#bonesPanel .part-row.on')?.scrollIntoView({ block: 'nearest' });
}

function render() {
  if (!s || $('#bonesPanel').hidden) return;
  const bones = viewer.bones;
  const f = s.filter.toLowerCase();
  const rows = bones.map((b, i) => [b, i]).filter(([b]) => !f || b.name.toLowerCase().includes(f));
  const posed = Object.keys(s.pose).length;
  const sel = bones[s.selected];
  $('#bonesPanel').replaceChildren(
    h('div', { class: 'parts-head' }, h('b', {}, t('Bones')),
      h('span', { class: 'muted' }, t('{n} bones. Pick one and turn it; double-click a slider to reset it.', { n: bones.length }))),
    h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: viewer.bonesShown, onchange: (e) => viewer.showBones(e.target.checked) }),
      h('span', {}, t('Show the skeleton'))),
    h('input', { class: 'search small-input', id: 'boneSearch', placeholder: t('Search bones'), value: s.filter,
      oninput: (e) => { s.filter = e.target.value; render(); $('#boneSearch').focus(); } }),
    h('div', { class: 'parts-items' }, ...rows.slice(0, MAX_ROWS).map(([b, i]) => h('div', {
      class: 'part-row' + (i === s.selected ? ' on' : ''), onclick: () => choose(i),
    }, h('span', { class: 'part-name', title: b.name }, b.name || `#${i}`), s.pose[i] && h('span', { class: 'muted' }, '●')))),
    sel ? h('div', { class: 'edit-section' }, h('b', { title: sel.name }, sel.name),
      slider('X', 0), slider('Y', 1), slider('Z', 2))
      : h('div', { class: 'muted small-note' }, t('Pick a bone in the list to pose it.')),
    h('div', { class: 'edit-row end' },
      h('button', { class: 'btn ghost small', id: 'boneReset', disabled: !posed,
        onclick: () => { s.pose = {}; viewer.resetPose(); render(); } },
        t('Reset pose')),
      h('button', { class: 'btn ghost small', onclick: closeBones }, t('Close'))),
  );
}

export function initBones() {
  $('#bonesBtn').onclick = () => ($('#bonesPanel').hidden ? openBones() : closeBones());
  on('viewer-info', (info) => {
    $('#bonesBtn').hidden = !info.bones;
    s = null;  // a new model: its own bones and pose
    if (!info.bones) closeBones();
    else if (!$('#bonesPanel').hidden) openBones();
  });
  on('viewer-closed', closeBones);
}
