// Первый запуск стенда: объекты пакета организаторов (реестр document_manifest.jsonl) и готовые ответы ML
// (out/submission_<объект>.json) как первая версия протокола — чтобы результат был виден сразу, без 1–4 минут прогона.
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { one, run, now, tx } from './db.js';
import { log } from './log.js';
import { readJsonl } from './domain.js';
import { recordImport } from './queue.js';

const NAMES = {
  'OBJ-RECHNIKOV-7-7': 'Речников ул. 7-7 (офисно-деловой центр)',
  'OBJ-NOVOSLOBODSKAYA': 'Новослободская (жилой дом с подземной автостоянкой)',
  'OBJ-TYUMENSKAYA-5-GOLD-SEED': 'Тюменская-5 (школа, пример нарушений на чертежах)',
};
const ROLE = { TEST_HIDDEN: 'TEST', TRAIN_PUBLIC: 'TRAIN' };

export function seedPackage() {
  const manifest = config.packageData && path.join(config.packageData, 'document_manifest.jsonl');
  if (!manifest || !fs.existsSync(manifest)) {
    log.info('пакет организаторов не подключён (PACKAGE_DATA) — объекты пакета не создаются');
    return;
  }
  const rows = readJsonl(manifest);
  const objects = [...new Set(rows.map((r) => r.object_id))];
  for (const objectId of objects) {
    const own = rows.filter((r) => r.object_id === objectId);
    const created = tx(() => {
      if (one('SELECT id FROM objects WHERE id = ?', objectId)) return false;
      run(`INSERT INTO objects(id, name, source, dataset_role, created_at) VALUES (?, ?, 'package', ?, ?)`,
        objectId, NAMES[objectId] || own[0].corpus || objectId, ROLE[own[0].split] || own[0].split || null, now());
      for (const r of own) {
        run(`INSERT INTO files(id, object_id, stage, section, relative_path, original_name, extension, size_bytes, sha256, source, manifest_row, uploaded_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'package', ?, ?)`,
        r.file_id, objectId, r.stage, r.section, r.relative_path, r.relative_path.split('/').pop(), r.extension, r.size_bytes,
        r.sha256, JSON.stringify(r), now());
      }
      return true;
    });
    if (created) log.info('объект пакета зарегистрирован', { object_id: objectId, files: own.length });

    const sub = config.seedResults && path.join(config.seedResults, `submission_${objectId}.json`);
    if (created && sub && fs.existsSync(sub)) {
      const integ = path.join(config.seedResults, `integrity_${objectId}.json`);
      const date = fs.statSync(sub).mtime.toISOString().slice(0, 10);
      const r = recordImport(objectId, sub, integ, `inspector-ml, готовый ответ от ${date}`);
      log.info('импортирован готовый ответ ML', { object_id: objectId, protocol_version: r.version, rows: r.rows });
    }
  }
}
