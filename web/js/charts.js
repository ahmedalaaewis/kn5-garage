// Line charts drawn as SVG elements (no markup strings).

import { s } from './dom.js';

const GRID = '#262d36';
const LABEL = '#8a96a3';

// series: [{points: [[x, y], …], color}]
// sharedY: one y scale for all series (compare), else each series fills the height (power vs torque)
export function lineChart({ series, width, height, pad, sharedY = false, xLabel = (v) => v, yLabels = false, cls }) {
  const P = { l: 8, r: 8, t: 10, b: 20, ...pad };
  const pts = series.flatMap((x) => x.points);
  const maxX = Math.max(...pts.map((p) => p[0]), 1);
  const sharedMax = Math.max(...pts.map((p) => p[1]), 1);
  const X = (v) => (P.l + (v / maxX) * (width - P.l - P.r)).toFixed(1);
  const Y = (v, max) => (height - P.b - (v / max) * (height - P.t - P.b)).toFixed(1);

  const grid = [0.25, 0.5, 0.75, 1].flatMap((f) => [
    s('line', { x1: P.l, x2: width - P.r, y1: Y(f * sharedMax, sharedMax), y2: Y(f * sharedMax, sharedMax),
      stroke: GRID, 'stroke-dasharray': '3 5' }),
    yLabels && s('text', { x: P.l - 6, y: +Y(f * sharedMax, sharedMax) + 4, fill: LABEL, 'font-size': 11,
      'text-anchor': 'end' }, Math.round(f * sharedMax)),
  ]);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => s('text', {
    x: X(f * maxX), y: height - 5, fill: LABEL, 'font-size': 11,
    'text-anchor': f === 0 ? 'start' : f === 1 ? 'end' : 'middle',
  }, xLabel(Math.round(f * maxX))));
  const lines = series.filter((x) => x.points.length).map((x) => {
    const max = sharedY ? sharedMax : Math.max(...x.points.map((p) => p[1]), 1);
    const d = x.points.map((p, i) => `${i ? 'L' : 'M'}${X(p[0])},${Y(p[1], max)}`).join('');
    return s('path', { d, fill: 'none', stroke: x.color, 'stroke-width': 2.3, 'stroke-linejoin': 'round',
      'stroke-linecap': 'round' });
  });
  return s('svg', { viewBox: `0 0 ${width} ${height}`, class: cls, preserveAspectRatio: sharedY ? null : 'none' },
    grid, ticks, lines);
}

export const peak = (pts) => (pts.length ? pts.reduce((a, b) => (b[1] > a[1] ? b : a)) : null);
