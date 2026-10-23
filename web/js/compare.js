// Side-by-side comparison of 2 to 6 selected cars.

import { api, q } from './api.js';
import { lineChart } from './charts.js';
import { $, h } from './dom.js';
import { t } from './i18n.js';
import { STATUS_LABEL, bestIndexes } from './model.js';
import { state } from './state.js';

const COLORS = ['#ff4d2e', '#4da3ff', '#2fd38a', '#ffb020', '#b07aff', '#ff7ac8'];

// key, label, true when higher is better
const ROWS = [
  ['bhp', 'Power', true], ['torque', 'Torque', true], ['weight', 'Weight', false],
  ['topspeed', 'Top speed', true], ['acceleration', '0–100', false], ['pwratio', 'Power/weight', false],
];

export async function openCompare() {
  const ids = [...state.checked].slice(0, 6);
  const body = $('#compareBody');
  body.replaceChildren(h('div', { class: 'muted' }, h('span', { class: 'spinner' }), ' Loading…'));
  $('#compareDlg').hidden = false;
  try {
    await Promise.all(ids.filter((id) => !state.details[id]).map(async (id) => {
      state.details[id] = await api(`/api/car?${q({ id })}`);
    }));
  } catch (e) { body.replaceChildren(h('div', {}, e.message)); return; }
  const cars = ids.map((id) => state.details[id]);
  body.replaceChildren(table(cars), curves(cars));
}

function table(cars) {
  const head = h('tr', {}, h('th', {}, ''), ...cars.map((d, i) => h('th', {},
    h('i', { class: 'swatch', style: `background:${COLORS[i]}` }), d.name,
    h('div', { class: 'muted small' }, [d.brand, d.year].filter(Boolean).join(' · ')))));
  const rows = ROWS.map(([key, label, higher]) => {
    const best = new Set(bestIndexes(cars.map((d) => d.specs?.[key]), higher));
    return h('tr', {}, h('th', {}, t(label)), ...cars.map((d, i) => h('td', { class: best.has(i) ? 'best' : '' },
      d.specs?.[key] || '—')));
  });
  const extra = [
    ['Author', (d) => d.author || '—'],
    ['Skins', (d) => d.skins.length],
    ['Drive', (d) => d.carData?.drive || '—'],
    ['Status', (d) => t(STATUS_LABEL[d.status] || d.status)],
  ].map(([label, f]) => h('tr', {}, h('th', {}, t(label)), ...cars.map((d) => h('td', {}, f(d)))));
  return h('div', { class: 'compare-wrap' },
    h('table', { class: 'compare' }, h('thead', {}, head), h('tbody', {}, ...rows, ...extra)));
}

function curves(cars) {
  const withCurve = cars.map((d, i) => [d, i]).filter(([d]) => d.powerCurve.length);
  if (!withCurve.length) return h('div', { class: 'muted' }, 'None of these cars has a power curve.');
  return h('div', { class: 'chart' },
    h('div', { class: 'chart-head' }, h('span', { class: 'chart-title' }, `${t('Power')} (hp / rpm)`),
      h('span', { class: 'legend' }, ...withCurve.map(([d, i]) => h('span', {}, h('i', { style: `background:${COLORS[i]}` }), d.name)))),
    lineChart({ width: 720, height: 240, pad: { l: 40, r: 10 }, sharedY: true, yLabels: true, cls: 'compare-chart',
      series: withCurve.map(([d, i]) => ({ points: d.powerCurve, color: COLORS[i] })) }));
}

export function initCompare() {
  $('#compareSel').onclick = openCompare;
}
