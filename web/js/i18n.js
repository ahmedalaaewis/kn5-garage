// Translations. The English text is the key, so anything not translated yet simply
// shows in English. Static page text is marked with data-i18n (text), data-i18n-title,
// data-i18n-placeholder; scripts call t('English text', {vars}).
//
// Each language lives in web/js/lang/<code>.js. To add one: copy a file there, translate the
// values (keep the {placeholders}), import it below and add it to LANGUAGES.

import ar from './lang/ar.js';
import de from './lang/de.js';
import es from './lang/es.js';
import fr from './lang/fr.js';
import it from './lang/it.js';
import ja from './lang/ja.js';
import ko from './lang/ko.js';
import pl from './lang/pl.js';
import pt from './lang/pt.js';
import ru from './lang/ru.js';
import tr from './lang/tr.js';
import zh from './lang/zh.js';

// In the order shown in the language menu (each in its own language)
export const LANGUAGES = {
  en: 'English', de: 'Deutsch', es: 'Español', fr: 'Français', it: 'Italiano', pl: 'Polski',
  pt: 'Português', tr: 'Türkçe', ru: 'Русский', ar: 'العربية', ja: '日本語', ko: '한국어', zh: '中文（简体）',
};
const RTL = new Set(['ar']);
const STRINGS = { ar, de, es, fr, it, ja, ko, pl, pt, ru, tr, zh };

let lang = 'en';

export function setLanguage(code) {
  lang = code in LANGUAGES ? code : 'en';
  document.documentElement.lang = lang;
  document.documentElement.dir = RTL.has(lang) ? 'rtl' : 'ltr';
  translatePage();
}

export const language = () => lang;

// The user's browser language, if we have it
export function preferredLanguage() {
  const code = (navigator.language || 'en').slice(0, 2).toLowerCase();
  return code in LANGUAGES ? code : 'en';
}

export function t(text, vars) {
  let out = (lang !== 'en' && STRINGS[lang]?.[text]) || text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return out;
}

// Static text in index.html: the English original is kept in data-i18n-src
export function translatePage(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) {
    el.dataset.i18nSrc ||= el.textContent.trim();
    el.textContent = t(el.dataset.i18nSrc);
  }
  for (const attr of ['title', 'placeholder']) {
    for (const el of root.querySelectorAll(`[data-i18n-${attr}]`)) {
      const key = `i18n${attr[0].toUpperCase()}${attr.slice(1)}Src`;
      el.dataset[key] ||= el.getAttribute(attr) || el.getAttribute(`data-i18n-${attr}`);
      el.setAttribute(attr, t(el.dataset[key]));
    }
  }
}

// For tests: every language and its strings
export const _strings = STRINGS;
