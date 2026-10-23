// Blender: the top-bar status, locating blender.exe, and opening an export in it.

import { api } from './api.js';
import { $ } from './dom.js';
import { t } from './i18n.js';
import { state } from './state.js';
import { flash } from './toast.js';

export async function refreshBlender() {
  try { state.blender = await api('/api/blender'); } catch (e) { state.blender = { found: false }; }
  $('#blenderState').textContent = t(state.blender.found ? 'found' : 'not found · locate');
  $('#blenderBtn').title = state.blender.found ? 'Using Blender at ' + state.blender.path
    : 'Blender was not found. Click to locate blender.exe';
  $('#blenderBtn').classList.toggle('missing', !state.blender.found);
}

async function locateBlender() {
  try {
    state.blender = await api('/api/blender/locate', {});
    await refreshBlender();
    if (state.blender.found) flash('Blender found. "Open in Blender" is ready.');
  } catch (e) { flash(e.message, true); }
}

export async function openInBlender(file) {
  try {
    const r = await api('/api/blender/open', { file });
    if (!r.launched) {
      flash(r.message, true, { label: 'Blender…', run: locateBlender });
      refreshBlender();
    } else flash(`Opening Blender with ${r.script}. It saves a .blend next to the export.`);
  } catch (e) { flash(e.message, true); }
}

export function initBlender() {
  $('#blenderBtn').onclick = () => (state.blender.found ? refreshBlender() : locateBlender());
  return refreshBlender();
}
