// DOM helpers. Car metadata comes from mod files, so it is only ever inserted as
// text (h() and s() create text nodes), never parsed as HTML.

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => [...document.querySelectorAll(sel)];

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

// h('div', {class: 'x', onclick: fn}, 'text', child, [more children])
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  if (tag === 'input' && attrs?.checked) el.checked = true;
  append(el, children);
  return el;
}

// The same for SVG elements
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

const stroke = { fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7' };
export const ICON = {
  lock: () => s('svg', { viewBox: '0 0 24 24' },
    s('rect', { x: 4.5, y: 10.5, width: 15, height: 10, rx: 2, ...stroke }),
    s('path', { d: 'M8 10.5V7.5a4 4 0 0 1 8 0v3', ...stroke })),
  alert: () => s('svg', { viewBox: '0 0 24 24' },
    s('path', { d: 'M12 3 2.5 20h19z', ...stroke, 'stroke-linejoin': 'round' }),
    s('path', { d: 'M12 10v4.5M12 17.2v.3', stroke: 'currentColor', 'stroke-width': '1.9', 'stroke-linecap': 'round' })),
  shield: () => s('svg', { viewBox: '0 0 24 24' },
    s('path', { d: 'M12 3 4.5 6v6c0 4.4 3.2 7.8 7.5 9 4.3-1.2 7.5-4.6 7.5-9V6z', ...stroke }),
    s('path', { d: 'm8.5 12 2.5 2.5 4.5-5', ...stroke })),
};

export function img(src, cls) {
  const el = h('img', { class: cls, src, alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' });
  el.addEventListener('error', () => el.remove());
  return el;
}

export function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), 3);
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function debounce(fn, ms) {
  let t = 0;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// Value of a segmented control: the data-p of its active button
export function segValue(sel) {
  return document.querySelector(`${sel} button.on`)?.dataset.p || 'tint';
}

export function wireSeg(sel, onChange) {
  $(sel).addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    $$(`${sel} button`).forEach((x) => x.classList.toggle('on', x === b));
    onChange?.(b);
  });
}

// Save text as a file in the browser's Downloads
export function downloadText(filename, text, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h('a', { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
