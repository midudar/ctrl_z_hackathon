// Аутентификация и роли (ТЗ, раздел 12): вход по логину и паролю, токен в заголовке Authorization: Bearer.
// Пароли — scrypt с солью, токен — JWT HS256 (подпись встроенным node:crypto, без сторонних библиотек).
import crypto from 'node:crypto';
import { config } from './config.js';
import { one, run, now, getSetting, setSetting } from './db.js';
import { HttpError } from './errors.js';

export const ROLES = {
  inspector: 'Инспектор',
  supervisor: 'Инспектор-супервизор',
  admin: 'Администратор',
  ml_engineer: 'ML-инженер',
};

// Кто что может. Инспектор — просмотр и верификация; супервизор — ещё отмена финализации;
// администратор — всё; ML-инженер — просмотр, логи обработки и данные для дообучения.
export const PERMISSIONS = {
  view: ['inspector', 'supervisor', 'admin', 'ml_engineer'],
  upload: ['inspector', 'supervisor', 'admin'],
  run: ['inspector', 'supervisor', 'admin'],
  decide: ['inspector', 'supervisor'],
  finalize: ['inspector', 'supervisor'],
  unfinalize: ['supervisor', 'admin'],
  manage_objects: ['inspector', 'supervisor', 'admin'],
  audit: ['supervisor', 'admin', 'ml_engineer'],
  ml_data: ['admin', 'ml_engineer'],
  users: ['admin'],
};

export function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(password, salt, 32);
  return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`;
}

export function checkPassword(password, stored) {
  const [alg, saltHex, hashHex] = String(stored).split('$');
  if (alg !== 'scrypt') return false;
  const hash = crypto.scryptSync(password, Buffer.from(saltHex, 'hex'), 32);
  return crypto.timingSafeEqual(hash, Buffer.from(hashHex, 'hex'));
}

// Секрет подписи: из JWT_SECRET или сгенерированный при первом запуске и сохранённый в БД
function secret() {
  if (config.jwtSecret) return config.jwtSecret;
  let s = getSetting('jwt_secret');
  if (!s) {
    s = crypto.randomBytes(32).toString('hex');
    setSetting('jwt_secret', s);
  }
  return s;
}

const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');

export function issueToken(user) {
  const payload = { sub: user.id, login: user.login, role: user.role, exp: Math.floor(Date.now() / 1000) + config.tokenTtlHours * 3600 };
  const body = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}`;
  const sig = crypto.createHmac('sha256', secret()).update(body).digest('base64url');
  return `${body}.${sig}`;
}

export function verifyToken(token) {
  const parts = String(token).split('.');
  if (parts.length !== 3) return null;
  const expected = crypto.createHmac('sha256', secret()).update(`${parts[0]}.${parts[1]}`).digest();
  const got = Buffer.from(parts[2], 'base64url');
  if (got.length !== expected.length || !crypto.timingSafeEqual(got, expected)) return null;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
    if (payload.exp < Date.now() / 1000) return null;
    return payload;
  } catch {
    return null;
  }
}

export function ensureDefaultUsers() {
  for (const u of config.defaultUsers) {
    if (!one('SELECT id FROM users WHERE login = ?', u.login)) {
      run('INSERT INTO users(login, full_name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)',
        u.login, u.full_name, u.role, hashPassword(u.password), now());
    }
  }
}

// Токен берётся из заголовка; для картинок страниц и скачивания файлов (теги <img>, ссылки) — ещё из ?token=
export function authenticate(req, _res, next) {
  const header = req.headers.authorization || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : req.query?.token;
  if (token) {
    const payload = verifyToken(token);
    if (payload) {
      const user = one('SELECT id, login, full_name, role, is_active FROM users WHERE id = ?', payload.sub);
      if (user && user.is_active) req.user = user;
    }
  }
  next();
}

export function requireAuth(req, _res, next) {
  if (!req.user) return next(new HttpError(401, 'UNAUTHORIZED', 'Требуется вход в систему'));
  next();
}

export function can(user, permission) {
  return Boolean(user && PERMISSIONS[permission]?.includes(user.role));
}

export function requirePermission(permission) {
  return (req, _res, next) => {
    if (!req.user) return next(new HttpError(401, 'UNAUTHORIZED', 'Требуется вход в систему'));
    if (!can(req.user, permission)) {
      return next(new HttpError(403, 'FORBIDDEN', `Недостаточно прав: действие доступно ролям ${PERMISSIONS[permission].map((r) => ROLES[r]).join(', ')}`));
    }
    next();
  };
}

export function publicUser(u) {
  const perms = Object.keys(PERMISSIONS).filter((p) => can(u, p));
  return { id: u.id, login: u.login, full_name: u.full_name, role: u.role, role_name: ROLES[u.role], permissions: perms };
}

// Защита от перебора паролей: не больше 10 неудачных входов с одного адреса за 5 минут
const failures = new Map();
export function loginThrottle(ip) {
  const t = Date.now();
  const list = (failures.get(ip) || []).filter((x) => t - x < 5 * 60 * 1000);
  failures.set(ip, list);
  return list.length >= 10;
}
export function loginFailed(ip) {
  failures.set(ip, [...(failures.get(ip) || []), Date.now()]);
}
