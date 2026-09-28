// Файлы объекта: реестр для ML (document_manifest.jsonl), путь к файлу на диске, приём загрузки.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { all, one, run, now } from './db.js';
import { catalogPath, schemaPath } from './domain.js';
import { HttpError } from './errors.js';

// Раздел по шифру в имени файла (как в ml/registry.py). ML и сам определяет дисциплину по имени —
// поле section нужно ему как второй источник.
const SECTION_RE = [
  ['KR', /(^|[^А-ЯA-Z])(КР|КЖ|КМ|КК|СВГ)\d*([^А-ЯA-Z]|$)/],
  ['OV', /(^|[^А-ЯA-Z])(ОВ|ИОС4)[\d.]*([^А-ЯA-Z]|$)/],
  ['AR', /(^|[^А-ЯA-Z])АР\d*([^А-ЯA-Z]|$)/],
  ['VK', /(^|[^А-ЯA-Z])(ВК|НВК|ИОС2|ИОС3)[\d.]*([^А-ЯA-Z]|$)/],
  ['EOM', /(^|[^А-ЯA-Z])(ЭОМ|ЭМ|ЭО|ЭН|ЭС|ИОС1)[\d.]*([^А-ЯA-Z]|$)/],
  ['SS', /(^|[^А-ЯA-Z])(СС|ИОС5)[\d.]*([^А-ЯA-Z]|$)/],
  ['GP', /(^|[^А-ЯA-Z])(ГП|ПЗУ)\d*([^А-ЯA-Z]|$)/],
  ['PB', /(^|[^А-ЯA-Z])(ПБ|ППМ|МОПБ)\d*([^А-ЯA-Z]|$)/],
  ['POS', /(^|[^А-ЯA-Z])ПОС\d*([^А-ЯA-Z]|$)/],
];
export const SECTIONS = ['KR', 'AR', 'OV', 'VK', 'EOM', 'SS', 'GP', 'PB', 'POS', 'OTHER'];

export function guessSection(name) {
  const upper = name.toUpperCase();
  for (const [section, re] of SECTION_RE) if (re.test(upper)) return section;
  return 'OTHER';
}

// Путь к файлу: у файлов пакета — относительно папки документов пакета, у загруженных — абсолютный
export function filePath(file) {
  if (path.isAbsolute(file.relative_path)) return file.relative_path;
  if (!config.packageDocs) throw new HttpError(404, 'DOCS_NOT_MOUNTED', 'Папка документов пакета не подключена (PACKAGE_DOCS)');
  return path.join(config.packageDocs, file.relative_path);
}

export function objectFiles(objectId) {
  return all('SELECT * FROM files WHERE object_id = ? ORDER BY stage, id', objectId);
}

// Строка реестра в формате ML (docs: раздел 3.2 документа для разработчиков)
function manifestRow(f, object) {
  if (f.manifest_row) return JSON.parse(f.manifest_row);
  return {
    schema_version: '0.1.0', file_id: f.id, object_id: f.object_id, corpus: object.name, dataset_role: 'UPLOADED',
    split: 'UPLOADED', relative_path: f.relative_path.replaceAll('\\', '/'), extension: f.extension, size_bytes: f.size_bytes,
    sha256: f.sha256, stage: f.stage, section: f.section || 'OTHER', pdf_pages: null, annotation_status: 'UNLABELED',
    exclusion_reason: null, duplicate_group: null, distribution_status: 'INCLUDE', label_visibility: 'UPLOADED',
  };
}

// Папка метаданных для прогона ML: реестр только этого объекта + матрица + схема ответа
export function writeObjectMeta(objectId) {
  const object = one('SELECT * FROM objects WHERE id = ?', objectId);
  const dir = path.join(config.dataDir, 'objects', objectId, 'meta');
  fs.mkdirSync(dir, { recursive: true });
  const rows = objectFiles(objectId).map((f) => JSON.stringify(manifestRow(f, object)));
  fs.writeFileSync(path.join(dir, 'document_manifest.jsonl'), rows.join('\n') + '\n', 'utf8');
  fs.copyFileSync(catalogPath(), path.join(dir, 'parameter_catalog_132.jsonl'));
  fs.copyFileSync(schemaPath(), path.join(dir, 'submission_schema.json'));
  return dir;
}

// Хэш входного набора файлов (ТЗ 10, Protocols.input_manifest_hash)
export function filesSnapshot(objectId) {
  const files = objectFiles(objectId).map((f) => ({ file_id: f.id, stage: f.stage, sha256: f.sha256, name: f.original_name }));
  const hash = crypto.createHash('sha256').update(files.map((f) => `${f.file_id}:${f.sha256}`).join('\n')).digest('hex');
  return { files, hash };
}

export function sha256File(p) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash('sha256');
    fs.createReadStream(p).on('data', (d) => h.update(d)).on('end', () => resolve(h.digest('hex'))).on('error', reject);
  });
}

// Проверка содержимого: PDF — заголовок и маркер конца, DOCX — ZIP, XML — начинается с «<»
function validateContent(p, ext) {
  const fd = fs.openSync(p, 'r');
  try {
    const size = fs.fstatSync(fd).size;
    if (!size) return 'файл пустой';
    const head = Buffer.alloc(Math.min(1024, size));
    fs.readSync(fd, head, 0, head.length, 0);
    if (ext === '.pdf') {
      if (!head.includes('%PDF-')) return 'файл не является PDF (нет заголовка %PDF)';
      const tail = Buffer.alloc(Math.min(2048, size));
      fs.readSync(fd, tail, 0, tail.length, size - tail.length);
      if (!tail.includes('%%EOF')) return 'PDF повреждён или загружен не полностью (нет маркера %%EOF)';
    } else if (ext === '.docx') {
      if (head.readUInt32LE(0) !== 0x04034b50) return 'файл не является документом DOCX';
    } else if (ext === '.xml') {
      const text = head.toString('utf8').replace(/^﻿/, '').trimStart();
      if (!text.startsWith('<')) return 'файл не является XML';
    }
    return null;
  } finally {
    fs.closeSync(fd);
  }
}

// multer/busboy отдаёт имена не-ASCII в latin1 — возвращаем UTF-8
export function fixName(name) {
  const utf = Buffer.from(name, 'latin1').toString('utf8');
  return /[Ѐ-ӿ]/.test(utf) && !utf.includes('�') ? utf : name;
}

function nextUploadId() {
  const row = one("SELECT id FROM files WHERE id LIKE 'U%' ORDER BY id DESC LIMIT 1");
  const n = row ? Number(row.id.slice(1)) + 1 : 1;
  return 'U' + String(n).padStart(5, '0');
}

const safeName = (n) => n.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 180);

// Принять загруженные файлы: проверка формата и содержимого, SHA-256, дубли, запись в реестр.
// Возвращает { accepted: [...], rejected: [{name, reason}] }
export async function acceptUploads(objectId, stage, section, uploaded, user) {
  const accepted = [];
  const rejected = [];
  const existing = new Map(objectFiles(objectId).map((f) => [f.sha256, f]));
  const dir = path.join(config.dataDir, 'uploads', objectId, stage);
  fs.mkdirSync(dir, { recursive: true });
  for (const u of uploaded) {
    const name = fixName(u.originalname);
    const ext = path.extname(name).toLowerCase();
    try {
      if (!config.allowedExt.includes(ext)) {
        rejected.push({ name, reason: `формат ${ext || 'без расширения'} не поддерживается; допустимые: PDF, DOCX, XML` });
        continue;
      }
      const bad = validateContent(u.path, ext);
      if (bad) {
        rejected.push({ name, reason: `${bad} — загрузите файл повторно` });
        continue;
      }
      const sha = await sha256File(u.path);
      if (existing.has(sha)) {
        const dup = existing.get(sha);
        rejected.push({ name, reason: `такой файл уже есть в объекте: ${dup.id} (${dup.original_name || path.basename(dup.relative_path)})` });
        continue;
      }
      let target = path.join(dir, safeName(name));
      for (let i = 2; fs.existsSync(target); i++) target = path.join(dir, `${path.parse(safeName(name)).name} (${i})${ext}`);
      fs.renameSync(u.path, target);
      const id = nextUploadId();
      const sec = section && section !== 'AUTO' ? section : guessSection(name);
      run(`INSERT INTO files(id, object_id, stage, section, relative_path, original_name, extension, size_bytes, sha256, source, uploaded_at, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'upload', ?, ?)`,
      id, objectId, stage, sec, path.resolve(target), name, ext, u.size, sha, now(), user.id);
      existing.set(sha, { id, original_name: name });
      accepted.push({ file_id: id, name, stage, section: sec, size_bytes: u.size, sha256: sha });
    } finally {
      if (fs.existsSync(u.path)) fs.rmSync(u.path, { force: true });
    }
  }
  return { accepted, rejected };
}

export function publicFile(f) {
  return {
    file_id: f.id, object_id: f.object_id, stage: f.stage, section: f.section,
    name: f.original_name || path.basename(f.relative_path), path: path.isAbsolute(f.relative_path) ? null : f.relative_path,
    extension: f.extension, size_bytes: f.size_bytes, sha256: f.sha256, source: f.source, uploaded_at: f.uploaded_at,
  };
}
