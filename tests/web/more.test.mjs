// Library extras and translations.  Run with:  node --test "tests/web/*.test.mjs"
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { LANGUAGES, _strings, setLanguage, t } from '../../web/js/i18n.js';
import { displayPath, filterCars, filterSkins, wastedBytes } from '../../web/js/model.js';

test('displayPath hides the user name', () => {
  assert.equal(displayPath('C:\\Users\\Jane\\Desktop\\cars', 'C:\\Users\\Jane'), '~\\Desktop\\cars');
  assert.equal(displayPath('c:\\users\\jane\\x', 'C:\\Users\\Jane'), '~\\x');  // Windows: any case
  assert.equal(displayPath('C:\\Users\\Janet\\x', 'C:\\Users\\Jane'), 'C:\\Users\\Janet\\x');  // not a prefix match
  assert.equal(displayPath('/home/jane/cars', '/home/jane/'), '~/cars');
  assert.equal(displayPath('/home/Jane/cars', '/home/jane'), '/home/Jane/cars');  // Linux: case matters
  assert.equal(displayPath('D:\\cars', 'C:\\Users\\Jane'), 'D:\\cars');
  assert.equal(displayPath('', 'x'), '');
  assert.equal(displayPath('D:\\cars', undefined), 'D:\\cars');
});

test('favourites filter and user tags in search', () => {
  const cars = [{ id: 'a', name: 'A', favourite: true, userTags: ['Le Mans'] }, { id: 'b', name: 'B', userTags: [] }];
  assert.deepEqual(filterCars(cars, 'fav').map((c) => c.id), ['a']);
  assert.deepEqual(filterCars(cars, 'all', 'le mans').map((c) => c.id), ['a']);
});

test('wastedBytes counts each identical group once, keeping the biggest', () => {
  const cars = [{ id: 'a', identical: ['b', 'c'] }, { id: 'b', identical: ['a', 'c'] }, { id: 'c', identical: ['a', 'b'] },
    { id: 'd', identical: [] }];
  assert.equal(wastedBytes(cars, { a: 100, b: 90, c: 80, d: 500 }), 170);
  assert.equal(wastedBytes([], {}), 0);
});

test('filterSkins searches names, drivers, teams, numbers and car names', () => {
  const skins = [{ id: 'red', name: 'Red', driver: 'A. Rossi', team: 'Velocità Racing', number: '17', car: 'gt3' },
    { id: 'blue', name: 'Blue', driver: 'M. Keller', team: 'Kessler', number: '7', car: 'rs' }];
  const ids = (q) => filterSkins(skins, q, (c) => ({ gt3: 'Corsa Rossa', rs: 'Nordlicht' }[c])).map((s) => s.id);
  assert.deepEqual(ids(''), ['red', 'blue']);
  assert.deepEqual(ids('rossi'), ['red']);
  assert.deepEqual(ids('17'), ['red']);
  assert.deepEqual(ids('nordlicht'), ['blue']);
  assert.deepEqual(ids('kessler'), ['blue']);
});

test('t() translates, fills placeholders and falls back to English', () => {
  globalThis.document = { documentElement: {}, querySelectorAll: () => [] };
  setLanguage('it');
  assert.equal(t('Scan'), 'Scansiona');
  assert.equal(t('{shown} of {total} in {folders} folders', { shown: 3, total: 9, folders: 2 }), '3 di 9 in 2 cartelle');
  assert.equal(t('Not a known string {x}', { x: 1 }), 'Not a known string 1');
  setLanguage('xx');
  assert.equal(t('Scan'), 'Scan');
  setLanguage('ar');
  assert.equal(document.documentElement.dir, 'rtl');
  assert.equal(t('{ok} of {n} exported', { ok: 2, n: 3 }), 'تم تصدير 2 من 3');
  setLanguage('ja');
  assert.equal(document.documentElement.dir, 'ltr');
  assert.equal(t('{n} triangles', { n: '3,976' }), '3,976 ポリゴン');
  setLanguage('en');
});

test('there are at least 12 languages besides English, each complete', () => {
  const codes = Object.keys(LANGUAGES).filter((l) => l !== 'en');
  assert.ok(codes.length >= 12, codes.join());
  const english = Object.keys(_strings.it);
  for (const code of codes) {
    assert.equal(Object.keys(_strings[code]).length, english.length, code);
    for (const key of english) assert.ok(_strings[code][key]?.trim(), `${code} is missing "${key}"`);
  }
});

test('every language translates the same strings with the same placeholders', () => {
  const langs = Object.keys(LANGUAGES).filter((l) => l !== 'en');
  const keys = new Set(Object.keys(_strings[langs[0]]));
  for (const lang of langs) {
    assert.deepEqual(new Set(Object.keys(_strings[lang])), keys, lang);
    for (const [en, tr] of Object.entries(_strings[lang])) {
      const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
      assert.equal(ph(tr), ph(en), `${lang}: ${en}`);
    }
  }
});
