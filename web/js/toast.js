// The message box in the bottom-left corner: info, errors and export progress.

import { $ } from './dom.js';
import { t } from './i18n.js';

let actionFn = null;
let cancelFn = null;

function show(title) {
  $('#toast').hidden = false;
  $('#toastTitle').textContent = title;
}

function setAction(action) {
  actionFn = action?.run || null;
  $('#toastAction').hidden = !action;
  if (action) $('#toastAction').textContent = action.label;
}

// action = {label, run} adds a button (e.g. "Show folder")
export function flash(msg, isError, action = null) {
  show(t(isError ? 'Something went wrong' : 'Info'));
  $('#toastMsg').textContent = msg;
  $('#toastBar').style.width = isError ? '0' : '100%';
  $('#toastCancel').hidden = true;
  setAction(action);
}

export function jobStarted(n, onCancel) {
  show(n > 1 ? t('Exporting {n} cars', { n }) : t('Exporting'));
  $('#toastBar').style.width = '0';
  $('#toastMsg').textContent = '…';
  $('#toastLog').textContent = '';
  cancelFn = onCancel;
  $('#toastCancel').hidden = false;
  setAction(null);
}

export function jobProgress(j) {
  $('#toastBar').style.width = `${Math.round(j.fraction * 100)}%`;
  $('#toastLog').textContent = j.log.join('\n');
  if (j.state !== 'done') {
    $('#toastMsg').textContent = `${j.current || ''} — ${j.message}  (${j.done}/${j.total})`;
    return;
  }
  const ok = j.results.filter((r) => r.ok).length;
  $('#toastTitle').textContent = t(j.cancelled ? 'Export cancelled' : 'Export finished');
  $('#toastMsg').textContent = t('{ok} of {n} exported', { ok, n: j.total });
  $('#toastCancel').hidden = true;
  cancelFn = null;
}

export function initToast() {
  $('#toastClose').onclick = () => { $('#toast').hidden = true; };
  $('#toastLogBtn').onclick = () => { $('#toastLog').hidden = !$('#toastLog').hidden; };
  $('#toastAction').onclick = () => actionFn?.();
  $('#toastCancel').onclick = () => {
    $('#toastCancel').disabled = true;
    $('#toastMsg').textContent = '…';
    cancelFn?.();
    setTimeout(() => { $('#toastCancel').disabled = false; }, 800);
  };
}
