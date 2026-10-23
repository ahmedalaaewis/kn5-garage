// Front-end logic tests. Run with:  node --test tests/web
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { bestIndexes, csvCell, filterCars, isBad, safeUrl, sortCars, specNum } from '../../web/js/model.js';

const cars = [
  { id: 'a', name: 'Alpha', brand: 'Zeta', status: 'ok', specs: { bhp: '500 bhp', weight: '1.200 kg' }, year: '2020', tags: ['gt3'] },
  { id: 'b', name: 'beta', brand: '', status: 'protected', specs: { bhp: '700bhp' }, duplicates: ['c'], issues: [] },
  { id: 'c', name: 'Gamma 10', brand: 'Acme', status: 'encrypted', specs: {}, duplicates: ['b'], issues: ['no skins folder'] },
  { id: 'd', name: 'Gamma 9', brand: 'Acme', status: 'no-model', specs: { bhp: '-' } },
];

test('specNum reads spec strings', () => {
  assert.equal(specNum('500 bhp'), 500);
  assert.equal(specNum('1.200 kg'), 1200);
  assert.equal(specNum('1,200kg'), 1200);
  assert.equal(specNum('3,1 s'), 3.1);
  assert.equal(specNum('2.45 kg/hp'), 2.45);
  assert.equal(specNum('-'), null);
  assert.equal(specNum(null), null);
  assert.equal(specNum(2024), 2024);
});

test('safeUrl only allows http(s) links', () => {
  assert.equal(safeUrl(' https://example.com/x '), 'https://example.com/x');
  assert.equal(safeUrl('http://a.b'), 'http://a.b');
  for (const bad of ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', 'file:///C:/x', 'data:text/html,x',
    'https://a.b/"onmouseover=x', 'https://a b', '//evil.example', 'ftp://a.b', '', null]) {
    assert.equal(safeUrl(bad), null, String(bad));
  }
});

test('csvCell escapes quotes and blocks spreadsheet formulas', () => {
  assert.equal(csvCell('plain'), 'plain');
  assert.equal(csvCell('a,b'), '"a,b"');
  assert.equal(csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(csvCell('line\nbreak'), '"line\nbreak"');
  assert.equal(csvCell('=HYPERLINK("http://evil")'), '"\'=HYPERLINK(""http://evil"")"');
  for (const lead of ['+', '-', '@']) assert.equal(csvCell(lead + 'cmd'), "'" + lead + 'cmd');
  assert.equal(csvCell(null), '');
  assert.equal(csvCell(0), '0');
});

test('filterCars by status, duplicates, issues and search', () => {
  const ids = (f, q) => filterCars(cars, f, q).map((c) => c.id);
  assert.deepEqual(ids('all'), ['a', 'b', 'c', 'd']);
  assert.deepEqual(ids('ok'), ['a']);
  assert.deepEqual(ids('protected'), ['b']);
  assert.deepEqual(ids('bad'), ['c', 'd']);
  assert.deepEqual(ids('dup'), ['b', 'c']);
  assert.deepEqual(ids('issues'), ['c']);
  assert.deepEqual(ids('all', 'GT3'), ['a']);
  assert.deepEqual(ids('all', 'acme'), ['c', 'd']);
  assert.deepEqual(ids('bad', 'gamma 9'), ['d']);
  assert.ok(isBad(cars[2]) && !isBad(cars[0]));
});

test('sortCars: natural names, numbers, blanks last both ways', () => {
  const order = (key, dir) => sortCars(cars, { key, dir }).map((c) => c.id);
  assert.deepEqual(order('name', 1), ['a', 'b', 'd', 'c']);  // Gamma 9 before Gamma 10
  assert.deepEqual(order('bhp', 1), ['a', 'b', 'c', 'd']);  // 500, 700, then the two without power
  assert.deepEqual(order('bhp', -1).slice(0, 2), ['b', 'a']);
  assert.deepEqual(order('brand', 1).slice(-1), ['b']);   // blank brand last ascending
  assert.deepEqual(order('brand', -1).slice(-1), ['b']);  // and descending
  assert.deepEqual(order('unknown-key', 1), order('name', 1));
  assert.equal(cars[0].id, 'a');  // input not changed
});

test('bestIndexes picks the best and ignores ties and blanks', () => {
  assert.deepEqual(bestIndexes(['500 bhp', '700 bhp', null], true), [1]);
  assert.deepEqual(bestIndexes(['1200 kg', '1100 kg'], false), [1]);
  assert.deepEqual(bestIndexes(['600 Nm', '600 Nm'], true), []);
  assert.deepEqual(bestIndexes(['600 Nm', null], true), []);
  assert.deepEqual(bestIndexes(['3.1 s', '2.9 s', '2.9 s'], false), [1, 2]);
});
