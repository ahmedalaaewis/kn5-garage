// Shared app state, and a tiny event bus so modules can ask for a re-render
// without importing each other ('grid', 'detail', 'exports', 'selection').

export const state = {
  settings: { roots: [], out: null, presets: {} },
  roots: [],
  blender: { found: false, path: null },
  cars: [],
  filter: 'all',
  kind: 'all',       // which kind of entries: all, ac, beamng, model
  search: '',
  layout: 'cards',   // or 'table'
  sort: { key: 'name', dir: 1 },
  selected: null,
  checked: new Set(),
  details: {},       // car id -> /api/car result
  skin: {},          // car id -> chosen skin id
  paintNames: {},    // car id -> material names chosen as paint in the viewer
  tab: 'overview',
  exports: [],
  exportIds: [],     // cars waiting in the export dialog
  preset: 'render',
  carJobs: {},       // car id -> {job, fraction, message, finished, result}
  done: {},          // car id -> latest successful result
};

const listeners = {};

export function on(event, fn) {
  (listeners[event] ||= []).push(fn);
}

export function emit(event, ...args) {
  for (const fn of listeners[event] || []) fn(...args);
}

export const carById = (id) => state.cars.find((c) => c.id === id);

// Remembered between visits (per browser); all access may fail in private mode
export const prefs = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem('kn5.' + key)) ?? fallback; } catch (e) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem('kn5.' + key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  },
};
