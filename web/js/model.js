// Pure logic with no DOM access (unit-tested with `node --test tests/web`).

export const STATUS_LABEL = { ok: 'Ready', protected: 'Protected', encrypted: 'Unreadable', 'no-model': 'No model' };
export const isBad = (c) => ['encrypted', 'no-model'].includes(c.status);

// Numbers out of spec strings like "500 bhp" / "1.200 kg" / "3,1 s"
export function specNum(v) {
  if (v == null) return null;
  const m = String(v).replace(/(\d)[.,](\d{3})(?!\d)/g, '$1$2').replace(',', '.').match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

// Only plain web links from mod files become clickable (no javascript:, file:, …)
export function safeUrl(u) {
  if (!u) return null;
  const str = String(u).trim();
  return /^https?:\/\/[^\s"'<>]+$/i.test(str) ? str : null;
}

// Paths on screen start with ~ instead of C:\Users\<name>, so screenshots and screen shares
// don't show the user name.
export function displayPath(path, home) {
  if (!path || !home) return path || '';
  const p = String(path), hm = String(home).replace(/[\\/]+$/, '');
  const same = /^[a-z]:/i.test(hm) ? p.toLowerCase().startsWith(hm.toLowerCase()) : p.startsWith(hm);
  return same && (p.length === hm.length || /[\\/]/.test(p[hm.length])) ? '~' + p.slice(hm.length) : p;
}

// A CSV cell. Text from mods starting with = + - @ would run as a formula in Excel,
// so it is prefixed with an apostrophe.
export function csvCell(v) {
  let str = v == null ? '' : String(v);
  if (/^[=+\-@\t\r]/.test(str)) str = "'" + str;
  return /[",\n\r]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
}

export function filterCars(cars, filter = 'all', search = '') {
  const q = search.toLowerCase();
  return cars.filter((c) => {
    if (filter === 'fav' && !c.favourite) return false;
    if (filter === 'ok' && c.status !== 'ok') return false;
    if (filter === 'protected' && c.status !== 'protected') return false;
    if (filter === 'bad' && !isBad(c)) return false;
    if (filter === 'dup' && !(c.duplicates || []).length) return false;
    if (filter === 'issues' && !(c.issues || []).length) return false;
    if (!q) return true;
    return [c.name, c.brand, c.author, c.id, c.year, c.carClass, ...(c.tags || []), ...(c.userTags || [])]
      .some((v) => v && String(v).toLowerCase().includes(q));
  });
}

export const COLUMNS = [
  ['name', 'Car', (c) => c.name],
  ['brand', 'Brand', (c) => c.brand || ''],
  ['year', 'Year', (c) => specNum(c.year)],
  ['bhp', 'Power', (c) => specNum(c.specs?.bhp)],
  ['weight', 'Weight', (c) => specNum(c.specs?.weight)],
  ['author', 'Author', (c) => c.author || ''],
  ['status', 'Status', (c) => c.status],
  ['skins', 'Skins', (c) => c.skinCount || 0],
  ['size', 'Model', (c) => c.kn5Size || 0],
];

// Blank values always go last, whatever the direction
export function sortCars(cars, sort = { key: 'name', dir: 1 }) {
  const col = COLUMNS.find((c) => c[0] === sort.key) || COLUMNS[0];
  const blank = (v) => v == null || v === '';
  return [...cars].sort((a, b) => {
    const x = col[2](a), y = col[2](b);
    if (blank(x)) return blank(y) ? 0 : 1;
    if (blank(y)) return -1;
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sort.dir;
  });
}

// Bytes that identical copies take beyond the first of each group (what removing them would free)
export function wastedBytes(cars, sizes) {
  const seen = new Set();
  let wasted = 0;
  for (const c of cars) {
    if (seen.has(c.id) || !(c.identical || []).length) continue;
    const group = [c.id, ...c.identical].filter((id) => id in sizes);
    group.forEach((id) => seen.add(id));
    const totals = group.map((id) => sizes[id]).sort((a, b) => b - a);
    wasted += totals.slice(1).reduce((a, b) => a + b, 0);
  }
  return wasted;
}

// Skins matching a search (name, driver, team, number, car)
export function filterSkins(skins, search, carName = (id) => id) {
  const q = search.trim().toLowerCase();
  if (!q) return skins;
  return skins.filter((s) => [s.name, s.driver, s.team, s.number, s.id, s.car, carName(s.car)]
    .some((v) => v && String(v).toLowerCase().includes(q)));
}

// Indexes of the best value in a compare row; none when all values are equal or missing
export function bestIndexes(values, higherIsBetter) {
  const nums = values.map(specNum);
  const valid = nums.filter((v) => v != null);
  if (new Set(valid).size < 2) return [];
  const best = higherIsBetter ? Math.max(...valid) : Math.min(...valid);
  return nums.map((v, i) => (v === best ? i : -1)).filter((i) => i >= 0);
}
