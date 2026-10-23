// Talking to the local server. Every call carries the per-run token from the page.

export const TOKEN = document.querySelector('meta[name="token"]').content;
const enc = encodeURIComponent;

export async function api(path, body) {
  const opts = { headers: { 'X-Token': TOKEN } };
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// Files are loaded by <img> / three.js, which can't send headers: the token goes in ?t=
export const fileUrl = (car, path) => `/api/file?car=${enc(car)}&path=${enc(path)}&t=${enc(TOKEN)}`;
export const outUrl = (name) => `/out/${enc(name)}?t=${enc(TOKEN)}`;
export const previewUrl = (serverUrl) => `/preview/${enc(serverUrl.replace(/^\/preview\//, ''))}?t=${enc(TOKEN)}`;
export const q = (params) => Object.entries(params).map(([k, v]) => `${enc(k)}=${enc(v ?? '')}`).join('&');
