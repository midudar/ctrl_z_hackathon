// База данных: SQLite (встроенный модуль node:sqlite, без нативной сборки). Таблицы — по ТЗ, раздел 10
// (Params, Objects, Files, Protocols, Checks, Evidence_Fragments, Audit_Log) плюс служебные: пользователи,
// процессы обработки (process_id) и решения инспектора.
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { config } from './config.js';

export const db = new DatabaseSync(path.join(config.dataDir, 'inspector.sqlite'));
db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;');

db.exec(`
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  login TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('inspector', 'supervisor', 'admin', 'ml_engineer')),
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

-- Матрица контроля (132 параметра + свободный поиск вне матрицы)
CREATE TABLE IF NOT EXISTS params (
  code TEXT PRIMARY KEY,
  param_id INTEGER,
  section TEXT,
  name TEXT,
  unit TEXT,
  source_pd TEXT,
  source_rd TEXT,
  source_id TEXT,
  trigger_text TEXT,
  criticality TEXT,
  in_matrix INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS objects (
  id TEXT PRIMARY KEY,                -- object_id, как в реестре ML
  name TEXT NOT NULL,
  address TEXT,
  developer TEXT,                     -- застройщик
  contractor TEXT,                    -- подрядчик
  case_number TEXT,                   -- номер надзорного дела
  permit_number TEXT,
  source TEXT NOT NULL,               -- package (пакет организаторов) | upload (создан в интерфейсе)
  dataset_role TEXT,                  -- TRAIN / TEST / null
  created_at TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,                -- file_id: F0001… из пакета, U00001… загруженные
  object_id TEXT NOT NULL REFERENCES objects(id),
  stage TEXT NOT NULL,                -- PD / RD / ID / RD_ID_MIXED / UNKNOWN
  section TEXT,
  relative_path TEXT NOT NULL,        -- относительно папки документов пакета или абсолютный путь загруженного файла
  original_name TEXT,
  extension TEXT,
  size_bytes INTEGER,
  sha256 TEXT,
  source TEXT NOT NULL,               -- package | upload
  manifest_row TEXT,                  -- исходная строка реестра (JSON) — для файлов пакета
  uploaded_at TEXT,
  uploaded_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS files_object ON files(object_id);

-- Процесс обработки: POST /documents/upload или запуск проверки → process_id → статус → результат
CREATE TABLE IF NOT EXISTS processes (
  id TEXT PRIMARY KEY,
  object_id TEXT NOT NULL REFERENCES objects(id),
  kind TEXT NOT NULL,                 -- inspection | import
  status TEXT NOT NULL,               -- PENDING / PARSING / DONE / FAILED
  message TEXT,
  progress REAL NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  exit_code INTEGER,
  error TEXT,
  log TEXT NOT NULL DEFAULT '',
  out_dir TEXT,
  requested_by INTEGER REFERENCES users(id),
  protocol_id INTEGER
);
CREATE INDEX IF NOT EXISTS processes_object ON processes(object_id);

-- Версии протоколов
CREATE TABLE IF NOT EXISTS protocols (
  id INTEGER PRIMARY KEY,
  object_id TEXT NOT NULL REFERENCES objects(id),
  process_id TEXT REFERENCES processes(id),
  version INTEGER NOT NULL,
  status TEXT NOT NULL,               -- READY / VERIFYING / VERIFICATION_COMPLETED / PROTOCOL_FINALIZED
  scenario TEXT,                      -- FULL / PD_RD_ONLY / … (ТЗ 9.2)
  matrix_version TEXT,
  dataset_version TEXT,
  model_version TEXT,
  input_manifest_hash TEXT,
  files_snapshot TEXT,                -- JSON: какие файлы были на входе
  integrity TEXT,                     -- JSON: отчёт целостности комплекта от ML
  submission_path TEXT,
  created_at TEXT NOT NULL,
  finalized_at TEXT,
  finalized_by INTEGER REFERENCES users(id),
  sync_status TEXT,                   -- передача в ИАИС «РиН»: null / PENDING_SYNC / SYNCED
  UNIQUE (object_id, version)
);

-- Результаты сопоставления: строка ответа ML = доказательная группа
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY,
  protocol_id INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
  evidence_group_id TEXT NOT NULL,
  parameter_code TEXT NOT NULL,
  location TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- candidate / negative / missing / manual / stub
  violation_label TEXT NOT NULL,      -- метка ответа ML
  finding_status TEXT NOT NULL,       -- статус ТЗ 9.2: CANDIDATE / NEGATIVE_VERIFIED / MISSING_EVIDENCE / NOT_COMPARABLE
  protocol_status TEXT,
  criticality TEXT,
  review_priority TEXT,               -- HIGH / MEDIUM / LOW — только очерёдность проверки
  pd_value TEXT,
  rd_value TEXT,
  id_value TEXT,
  note TEXT,
  UNIQUE (protocol_id, parameter_code, location)
);
CREATE INDEX IF NOT EXISTS checks_protocol ON checks(protocol_id, kind);

CREATE TABLE IF NOT EXISTS evidence_fragments (
  id INTEGER PRIMARY KEY,
  check_id INTEGER NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  stage TEXT NOT NULL,
  file_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  bbox_norm TEXT,                     -- JSON [x0, y0, x1, y1] в долях видимой страницы
  bbox_source TEXT,
  fragment TEXT
);
CREATE INDEX IF NOT EXISTS evidence_check ON evidence_fragments(check_id);

-- Решения инспектора (ТЗ 9.3). Нет строки — кандидат ожидает решения (PENDING)
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  check_id INTEGER UNIQUE NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('CONFIRMED_VIOLATION', 'NEGATIVE_VERIFIED', 'CLARIFICATION_REQUIRED')),
  reason_code TEXT,
  comment TEXT,
  recommendation TEXT,
  user_id INTEGER REFERENCES users(id),
  decided_at TEXT NOT NULL,
  carried_from_version INTEGER        -- решение перенесено из предыдущей версии протокола при дозагрузке
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  user_id INTEGER,
  user_login TEXT,
  action TEXT NOT NULL,
  object_id TEXT,
  details TEXT,
  ip TEXT,
  user_agent TEXT,
  request_id TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
`);

// Миграции существующей базы: новые столбцы добавляются, если их ещё нет
function addColumn(table, column, type) {
  const cols = db.prepare(`PRAGMA table_info(${table})`).all().map((c) => c.name);
  if (!cols.includes(column)) db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
}
addColumn('decisions', 'carried_note', 'TEXT');     // решение перенесено, но данные ИД изменились

export const now = () => new Date().toISOString();

// Транзакция: fn выполняется целиком или не выполняется вовсе
export function tx(fn) {
  db.exec('BEGIN');
  try {
    const r = fn();
    db.exec('COMMIT');
    return r;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

export const one = (sql, ...args) => db.prepare(sql).get(...args);
export const all = (sql, ...args) => db.prepare(sql).all(...args);
export const run = (sql, ...args) => db.prepare(sql).run(...args);

export function getSetting(key) {
  return one('SELECT value FROM settings WHERE key = ?', key)?.value;
}
export function setSetting(key, value) {
  run('INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value', key, value);
}
