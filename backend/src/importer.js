// Ответ ML (submission.json + отчёт целостности) → новая версия протокола в БД.
// При дозагрузке решения инспектора переносятся из предыдущей версии, если строка не изменилась
// (ТЗ 9.3: «дозагрузить файлы без сброса верификации»).
import fs from 'node:fs';
import { one, run, tx, now, all } from './db.js';
import {
  kindOf, FINDING_STATUS, reviewPriority, evidenceGroupId, matrixVersion, stageStatus, scenario,
  refreshProtocolStatus, params,
} from './domain.js';
import { filesSnapshot, objectFiles } from './files.js';

const same = (a, b) => (a ?? null) === (b ?? null);

export function importSubmission({ objectId, processId, submission, integrity, modelVersion, submissionPath }) {
  const P = params();
  return tx(() => {
    const prev = one('SELECT * FROM protocols WHERE object_id = ? ORDER BY version DESC LIMIT 1', objectId);
    const version = prev ? prev.version + 1 : 1;
    const snap = filesSnapshot(objectId);
    const stages = stageStatus(objectFiles(objectId), integrity);
    const res = run(`INSERT INTO protocols(object_id, process_id, version, status, scenario, matrix_version, dataset_version,
                       model_version, input_manifest_hash, files_snapshot, integrity, submission_path, created_at)
                     VALUES (?, ?, ?, 'READY', ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    objectId, processId, version, scenario(stages), matrixVersion(),
    'GOLD-черновик: решения инспекторов (обучение не проводилось)', modelVersion, snap.hash,
    JSON.stringify(snap.files), JSON.stringify(integrity || null), submissionPath || null, now());
    const protocolId = Number(res.lastInsertRowid);

    // Решения предыдущей версии: ключ — параметр + место (стабилен между прогонами)
    const prevRows = prev ? new Map(all(`SELECT c.*, d.status AS d_status, d.reason_code, d.comment, d.recommendation, d.user_id, d.decided_at
                                          FROM checks c JOIN decisions d ON d.check_id = c.id WHERE c.protocol_id = ?`, prev.id)
      .map((r) => [`${r.parameter_code}\u0000${r.location}`, r])) : new Map();

    let carried = 0;
    let reset = 0;
    for (const c of submission.checks) {
      const kind = kindOf(c);
      const criticality = c.criticality || P.get(c.parameter_code)?.criticality || null;
      const r = run(`INSERT INTO checks(protocol_id, evidence_group_id, parameter_code, location, kind, violation_label, finding_status,
                       protocol_status, criticality, review_priority, pd_value, rd_value, id_value, note)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      protocolId, evidenceGroupId(objectId, c.parameter_code, c.location), c.parameter_code, c.location ?? '', kind,
      c.violation_label, FINDING_STATUS[kind], c.protocol_status ?? null, criticality, reviewPriority(kind, criticality),
      c.pd_value ?? null, c.rd_value ?? null, c.id_value ?? null, c.note ?? null);
      const checkId = Number(r.lastInsertRowid);
      (c.evidence || []).forEach((e, i) => {
        run(`INSERT INTO evidence_fragments(check_id, ord, stage, file_id, page, bbox_norm, bbox_source, fragment)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        checkId, i, e.stage, e.file_id, e.pdf_page_number, e.bbox_norm ? JSON.stringify(e.bbox_norm) : null,
        e.bbox_source ?? null, e.fragment ?? null);
      });
      // Решение переносится, если не изменились метка и значения ПД / РД — предмет решения тот же. Новые данные ИД
      // (дозагрузка актов) решение не сбрасывают (ТЗ 9.3: дозагрузка «без сброса верификации»), но помечаются.
      const old = prevRows.get(`${c.parameter_code}\u0000${c.location ?? ''}`);
      if (old) {
        const same_subject = old.violation_label === c.violation_label && same(old.pd_value, c.pd_value)
          && same(old.rd_value, c.rd_value);
        if (same_subject) {
          const note = same(old.id_value, c.id_value) ? null
            : `после дозагрузки изменились данные ИД: ${old.id_value ?? '—'} → ${c.id_value ?? '—'}; проверьте решение`;
          run(`INSERT INTO decisions(check_id, status, reason_code, comment, recommendation, user_id, decided_at, carried_from_version, carried_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          checkId, old.d_status, old.reason_code, old.comment, old.recommendation, old.user_id, old.decided_at, prev.version, note);
          carried += 1;
        } else {
          reset += 1;
        }
      }
    }
    const status = refreshProtocolStatus(protocolId);
    return { protocolId, version, status, carried, reset, rows: submission.checks.length };
  });
}

export function readJson(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}
