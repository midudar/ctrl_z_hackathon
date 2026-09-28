// Клиент REST API. Токен хранится в localStorage (удобство одного браузера; при ошибке доступа — просто нет входа).
const BASE = '/api/v1';
const KEY = 'inspector.token';

export function getToken() {
  try { return localStorage.getItem(KEY); } catch { return null; }
}
export function setToken(t) {
  try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch { /* приватный режим */ }
}

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Ошибка ${status}`);
    this.status = status;
    this.code = body?.code;
    this.details = body?.details;
    this.body = body;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, { method = 'GET', body, form, raw } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const res = await fetch(BASE + path, { method, headers, body: payload });
  if (res.status === 401 && path !== '/auth/login') onUnauthorized();
  if (raw) {
    if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => ({})));
    return res;
  }
  const data = res.status === 204 ? null : await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// Ссылки для <img> и скачивания: токен в query (тегу <img> заголовок не передать)
export function pageUrl(fileId, page, { max = 2000, region } = {}) {
  const q = new URLSearchParams({ max: String(max), token: getToken() || '' });
  if (region) q.set('region', region.map((v) => v.toFixed(4)).join(','));
  return `${BASE}/files/${encodeURIComponent(fileId)}/pages/${page}?${q}`;
}
export function fileUrl(fileId) {
  return `${BASE}/files/${encodeURIComponent(fileId)}/download?token=${encodeURIComponent(getToken() || '')}`;
}

// Скачать протокол: запрос с заголовком, файл — через blob
export async function download(path, fallbackName) {
  const res = await api(path, { raw: true });
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return { name, ms: Number(res.headers.get('X-Generation-Ms')) || null };
}
