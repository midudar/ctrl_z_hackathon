// REST API /api/v1. Контракт — openapi.yaml (OpenAPI 3.0), запросы проверяются по нему (app.js).
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import express from 'express';
import multer from 'multer';
import { config } from './config.js';
import { one, all, run, now, tx } from './db.js';
import { HttpError } from './errors.js';
import { audit, ACTIONS } from './audit.js';
import {
  requireAuth, requirePermission, can, checkPassword, issueToken, publicUser, loginThrottle, loginFailed, ROLES,
} from './auth.js';
import {
  params, protocolRows, summarize, stageStatus, shortSection, refreshProtocolStatus, processStatus, indicator,
  recommendationTemplate, deviationText, REASON_CODES, DECISIONS, KIND_NAMES, SCENARIO_NAMES, PROTOCOL_STATUS_NAMES,
  STAGE_NAMES, CRITICAL, methodOf,
} from './domain.js';
import { acceptUploads, objectFiles, publicFile, filePath, SECTIONS } from './files.js';
import { createInspection, queueMode } from './queue.js';
import { pageImage, buildProtocol } from './ml.js';
import { protocolPayload, protocolXml, evidenceOf } from './protocol-data.js';
import { metrics } from './metrics.js';

export const api = express.Router();

const need = (v, status, code, msg) => {
  if (!v) throw new HttpError(status, code, msg);
  return v;
};

// ---------- Вход ----------

api.post('/auth/login', (req, res) => {
  const ip = req.socket.remoteAddress;
  if (loginThrottle(ip)) throw new HttpError(429, 'TOO_MANY_ATTEMPTS', 'Слишком много неудачных попыток входа, повторите через 5 минут');
  const { login, password } = req.body;
  const user = one('SELECT * FROM users WHERE login = ? AND is_active = 1', login);
  if (!user || !checkPassword(password, user.password_hash)) {
    loginFailed(ip);
    metrics.counters.logins_failed_total += 1;
    audit(req, 'LOGIN_FAILED', null, { login });
    throw new HttpError(401, 'INVALID_CREDENTIALS', 'Неверный логин или пароль');
  }
  req.user = user;
  metrics.counters.logins_total += 1;
  audit(req, 'LOGIN');
  res.json({ token: issueToken(user), user: publicUser(user) });
});

api.use(requireAuth);

api.get('/auth/me', (req, res) => res.json(publicUser(req.user)));

// ---------- Справочники ----------

api.get('/reference', (_req, res) => {
  res.json({
    reason_codes: REASON_CODES, decisions: DECISIONS, kinds: KIND_NAMES, scenarios: SCENARIO_NAMES,
    protocol_statuses: PROTOCOL_STATUS_NAMES, stages: STAGE_NAMES, sections: SECTIONS, roles: ROLES, queue_mode: queueMode(),
    limits: { max_file_mb: config.maxFileMb, max_batch_mb: config.maxBatchMb, formats: config.allowedExt },
  });
});

api.get('/params', (_req, res) => {
  res.json([...params().values()].sort((a, b) => a.param_id - b.param_id).map((p) => ({
    code: p.code, param_id: p.param_id, section: p.section, section_short: shortSection(p.section), name: p.name, unit: p.unit,
    source_pd: p.source_pd, source_rd: p.source_rd, source_id: p.source_id, trigger: p.trigger_text, criticality: p.criticality,
    in_matrix: Boolean(p.in_matrix),
  })));
});

// ---------- Объекты ----------

function latestProtocol(objectId) {
  return one('SELECT * FROM protocols WHERE object_id = ? ORDER BY version DESC LIMIT 1', objectId);
}
function latestProcess(objectId) {
  return one('SELECT * FROM processes WHERE object_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1', objectId);
}

function objectView(o, withDetails = false) {
  const files = objectFiles(o.id);
  const byStage = { PD: 0, RD: 0, ID: 0 };
  for (const f of files) {
    if (f.stage === 'RD_ID_MIXED') { byStage.RD += 1; byStage.ID += 1; } else if (f.stage in byStage) byStage[f.stage] += 1;
  }
  const prot = latestProtocol(o.id);
  const summary = prot ? summarize(protocolRows(prot.id)) : null;
  const proc = latestProcess(o.id);
  const view = {
    object_id: o.id, name: o.name, address: o.address, developer: o.developer, contractor: o.contractor,
    case_number: o.case_number, permit_number: o.permit_number, source: o.source, dataset_role: o.dataset_role,
    created_at: o.created_at, files: { total: files.length, ...byStage },
    indicator: indicator(summary),
    protocol: prot ? {
      protocol_id: prot.id, version: prot.version, status: prot.status, status_text: PROTOCOL_STATUS_NAMES[prot.status],
      created_at: prot.created_at, candidates: summary.candidates, confirmed: summary.confirmed, parameters: summary.parameters,
    } : null,
    process: proc ? processView(proc, false) : null,
  };
  if (withDetails && prot) {
    view.stages = stageStatus(files, JSON.parse(prot.integrity || 'null'));
  } else if (withDetails) {
    view.stages = stageStatus(files, null);
  }
  return view;
}

api.get('/objects', (_req, res) => {
  res.json(all('SELECT * FROM objects ORDER BY created_at, id').map((o) => objectView(o)));
});

api.post('/objects', requirePermission('manage_objects'), (req, res) => {
  const b = req.body;
  const id = b.object_id || `OBJ-U-${crypto.randomBytes(3).toString('hex').toUpperCase()}`;
  if (one('SELECT id FROM objects WHERE id = ?', id)) throw new HttpError(409, 'OBJECT_EXISTS', `Объект ${id} уже есть`);
  run(`INSERT INTO objects(id, name, address, developer, contractor, case_number, permit_number, source, created_at, created_by)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'upload', ?, ?)`,
  id, b.name.trim(), b.address ?? null, b.developer ?? null, b.contractor ?? null, b.case_number ?? null, b.permit_number ?? null, now(), req.user.id);
  audit(req, 'OBJECT_CREATE', id, { name: b.name });
  res.status(201).json(objectView(one('SELECT * FROM objects WHERE id = ?', id), true));
});

function getObject(id) {
  return need(one('SELECT * FROM objects WHERE id = ?', id), 404, 'OBJECT_NOT_FOUND', `Объект ${id} не найден`);
}

api.get('/objects/:objectId', (req, res) => {
  res.json(objectView(getObject(req.params.objectId), true));
});

api.patch('/objects/:objectId', requirePermission('manage_objects'), (req, res) => {
  const o = getObject(req.params.objectId);
  const fields = ['name', 'address', 'developer', 'contractor', 'case_number', 'permit_number'].filter((f) => f in req.body);
  for (const f of fields) run(`UPDATE objects SET ${f} = ? WHERE id = ?`, req.body[f], o.id);
  audit(req, 'OBJECT_UPDATE', o.id, Object.fromEntries(fields.map((f) => [f, req.body[f]])));
  res.json(objectView(getObject(o.id), true));
});

api.get('/objects/:objectId/files', (req, res) => {
  getObject(req.params.objectId);
  res.json(objectFiles(req.params.objectId).map(publicFile));
});

// ---------- Загрузка документов → process_id (ТЗ 9.6: POST /api/v1/documents/upload) ----------

const upload = multer({
  dest: path.join(config.dataDir, 'tmp'),
  limits: { fileSize: config.maxFileMb * 1024 * 1024, files: 200 },
});

function assertNotFinalized(objectId, what) {
  const prot = latestProtocol(objectId);
  if (prot?.status === 'PROTOCOL_FINALIZED') {
    throw new HttpError(409, 'PROTOCOL_FINALIZED',
      `Протокол версии ${prot.version} финализирован: ${what} невозможна. Отменить финализацию может супервизор или администратор.`);
  }
}

api.post('/documents/upload', requirePermission('upload'), (req, res, next) => {
  upload.array('files')(req, res, (err) => {
    if (err?.code === 'LIMIT_FILE_SIZE') return next(new HttpError(413, 'FILE_TOO_LARGE', `Файл превышает ${config.maxFileMb} МБ — максимальный размер одного файла ${config.maxFileMb} МБ`));
    if (err) return next(new HttpError(400, 'UPLOAD_ERROR', `Ошибка загрузки: ${err.message}`));
    next();
  });
}, async (req, res) => {
  const cleanup = () => (req.files || []).forEach((f) => fs.rmSync(f.path, { force: true }));
  try {
    const { object_id: objectId, stage } = req.body;
    const section = req.body.section || 'AUTO';
    const start = req.body.start !== 'false';
    if (!objectId || !one('SELECT id FROM objects WHERE id = ?', objectId)) throw new HttpError(404, 'OBJECT_NOT_FOUND', `Объект ${objectId} не найден`);
    if (!['PD', 'RD', 'ID'].includes(stage)) throw new HttpError(400, 'BAD_STAGE', 'Стадия документов: PD, RD или ID');
    if (section !== 'AUTO' && !SECTIONS.includes(section)) throw new HttpError(400, 'BAD_SECTION', `Раздел: AUTO или ${SECTIONS.join(', ')}`);
    if (!req.files?.length) throw new HttpError(400, 'NO_FILES', 'Не выбраны файлы');
    const total = req.files.reduce((s, f) => s + f.size, 0);
    if (total > config.maxBatchMb * 1024 * 1024) {
      throw new HttpError(413, 'BATCH_TOO_LARGE', `Общий объём пакета ${(total / 1048576).toFixed(0)} МБ превышает лимит ${config.maxBatchMb} МБ`);
    }
    assertNotFinalized(objectId, 'дозагрузка');
    const { accepted, rejected } = await acceptUploads(objectId, stage, section, req.files, req.user);
    if (rejected.length) audit(req, 'FILES_REJECTED', objectId, { rejected });
    if (!accepted.length) {
      return res.status(422).json({ code: 'ALL_FILES_REJECTED', message: 'Ни один файл не принят', accepted, rejected });
    }
    audit(req, 'FILES_UPLOAD', objectId, { stage, files: accepted.map((f) => `${f.file_id} ${f.name}`) });
    let processId = null;
    if (start) {
      processId = createInspection(objectId, req.user);
      audit(req, 'INSPECTION_START', objectId, { process_id: processId, reason: 'загрузка файлов' });
    }
    res.status(202).json({ process_id: processId, object_id: objectId, accepted, rejected });
  } finally {
    cleanup();
  }
});

api.post('/objects/:objectId/inspections', requirePermission('run'), (req, res) => {
  const o = getObject(req.params.objectId);
  assertNotFinalized(o.id, 'новая проверка');
  if (!objectFiles(o.id).length) throw new HttpError(409, 'NO_FILES', 'У объекта нет файлов — сначала загрузите документы');
  const active = one("SELECT id FROM processes WHERE object_id = ? AND status IN ('PENDING', 'PARSING')", o.id);
  if (active) throw new HttpError(409, 'ALREADY_RUNNING', 'Проверка этого объекта уже идёт', { process_id: active.id });
  const id = createInspection(o.id, req.user);
  audit(req, 'INSPECTION_START', o.id, { process_id: id });
  res.status(202).json({ process_id: id, object_id: o.id, status: 'PENDING' });
});

// ---------- Процессы: статус и результат (pull-модель, ТЗ 1.4) ----------

function processView(p, withLog, user) {
  const prot = p.protocol_id ? one('SELECT * FROM protocols WHERE id = ?', p.protocol_id) : null;
  const logText = p.log || '';
  const full = withLog && can(user, 'ml_data');
  return {
    process_id: p.id, object_id: p.object_id, kind: p.kind, status: processStatus(p, prot), processing_status: p.status,
    message: p.message, progress: p.progress, attempts: p.attempts, created_at: p.created_at, started_at: p.started_at,
    finished_at: p.finished_at, exit_code: p.exit_code, error: p.error,
    protocol_id: prot?.id ?? null, protocol_version: prot?.version ?? null,
    ...(withLog ? { log: full ? logText : logText.split('\n').slice(-40).join('\n'), log_truncated: !full } : {}),
  };
}

api.get('/processes', (req, res) => {
  const rows = req.query.object_id
    ? all('SELECT * FROM processes WHERE object_id = ? ORDER BY created_at DESC', req.query.object_id)
    : all('SELECT * FROM processes ORDER BY created_at DESC LIMIT 100');
  res.json(rows.map((p) => processView(p, false)));
});

function getProcess(id) {
  return need(one('SELECT * FROM processes WHERE id = ?', id), 404, 'PROCESS_NOT_FOUND', `Процесс ${id} не найден`);
}

api.get('/processes/:processId', (req, res) => res.json(processView(getProcess(req.params.processId), true, req.user)));

api.get('/processes/:processId/result', (req, res) => {
  const p = getProcess(req.params.processId);
  if (p.status === 'FAILED') throw new HttpError(409, 'PROCESS_FAILED', `Обработка завершилась ошибкой: ${p.error}`);
  if (p.status !== 'DONE') return res.status(202).json({ process_id: p.id, status: p.status, message: 'Результат ещё не готов' });
  res.json(submissionFromDb(p.protocol_id));
});

// Ответ ML в формате submission.json — из БД (совпадает с файлом ответа, плюс решения инспектора)
function submissionFromDb(protocolId) {
  const prot = one('SELECT * FROM protocols WHERE id = ?', protocolId);
  const rows = protocolRows(protocolId);
  const ev = evidenceOf(rows.map((r) => r.id));
  return {
    object_id: prot.object_id, protocol_version: prot.version,
    checks: rows.map((r) => ({
      parameter_code: r.parameter_code, location: r.location, violation_label: r.violation_label,
      protocol_status: r.protocol_status, criticality: r.criticality, pd_value: r.pd_value, rd_value: r.rd_value, id_value: r.id_value,
      evidence: (ev.get(r.id) || []).map((e) => ({
        stage: e.stage, file_id: e.file_id, pdf_page_number: e.page, bbox_norm: e.bbox_norm, bbox_source: e.bbox_source, fragment: e.fragment,
      })),
      ...(r.note ? { note: r.note } : {}),
      finding_status: r.finding_status,
      inspector_decision: r.decision_status ? { status: r.decision_status, reason_code: r.reason_code, comment: r.decision_comment, decided_at: r.decided_at } : null,
    })),
    document_integrity: JSON.parse(prot.integrity || 'null'),
  };
}

// ---------- Протоколы ----------

function getProtocol(id) {
  return need(one('SELECT * FROM protocols WHERE id = ?', Number(id)), 404, 'PROTOCOL_NOT_FOUND', `Протокол ${id} не найден`);
}

api.get('/objects/:objectId/protocols', (req, res) => {
  getObject(req.params.objectId);
  res.json(all('SELECT * FROM protocols WHERE object_id = ? ORDER BY version DESC', req.params.objectId).map((p) => {
    const s = summarize(protocolRows(p.id));
    return {
      protocol_id: p.id, version: p.version, status: p.status, status_text: PROTOCOL_STATUS_NAMES[p.status], created_at: p.created_at,
      finalized_at: p.finalized_at, process_id: p.process_id, candidates: s.candidates, confirmed: s.confirmed,
    };
  }));
});

api.get('/protocols/:protocolId', (req, res) => {
  const p = getProtocol(req.params.protocolId);
  const rows = protocolRows(p.id);
  const summary = summarize(rows);
  const latest = latestProtocol(p.object_id);
  const files = JSON.parse(p.files_snapshot || '[]').map((f) => ({ ...f, id: f.file_id }));
  const integrity = JSON.parse(p.integrity || 'null');
  const finalizer = p.finalized_by ? one('SELECT full_name FROM users WHERE id = ?', p.finalized_by) : null;
  const isLatest = latest.id === p.id;
  const finalized = p.status === 'PROTOCOL_FINALIZED';
  res.json({
    protocol_id: p.id, object_id: p.object_id, version: p.version, is_latest: isLatest, status: p.status,
    status_text: PROTOCOL_STATUS_NAMES[p.status], scenario: p.scenario, scenario_text: SCENARIO_NAMES[p.scenario],
    created_at: p.created_at, finalized_at: p.finalized_at, finalized_by: finalizer?.full_name ?? null, process_id: p.process_id,
    matrix_version: p.matrix_version, model_version: p.model_version, dataset_version: p.dataset_version,
    input_manifest_hash: p.input_manifest_hash, sync_status: p.sync_status,
    summary, stages: stageStatus(files, integrity), integrity: integrity?.findings || [],
    actions: {
      decide: isLatest && !finalized && can(req.user, 'decide'),
      finalize: isLatest && !finalized && can(req.user, 'finalize') && summary.candidates.decisions.PENDING === 0,
      unfinalize: isLatest && finalized && can(req.user, 'unfinalize'),
      upload: isLatest && !finalized && can(req.user, 'upload'),
    },
  });
});

function checkView(r, ev, P) {
  const prm = P.get(r.parameter_code) || {};
  return {
    check_id: r.id, evidence_group_id: r.evidence_group_id, parameter_code: r.parameter_code, parameter_name: prm.name || r.parameter_code,
    section: shortSection(prm.section), param_id: prm.param_id ?? null, location: r.location, kind: r.kind,
    violation_label: r.violation_label, finding_status: r.finding_status, protocol_status: r.protocol_status,
    criticality: r.criticality, critical: r.criticality === CRITICAL, review_priority: r.review_priority,
    pd_value: r.pd_value, rd_value: r.rd_value, id_value: r.id_value, note: r.note,
    decision: r.decision_status ? {
      status: r.decision_status, reason_code: r.reason_code, comment: r.decision_comment, recommendation: r.recommendation,
      decided_by: r.decided_by, decided_at: r.decided_at, carried_from_version: r.carried_from_version, carried_note: r.carried_note,
    } : null,
    evidence: (ev.get(r.id) || []).map((e) => ({
      stage: e.stage, file_id: e.file_id, file_name: e.file_name, page: e.page, bbox_norm: e.bbox_norm, bbox_source: e.bbox_source, fragment: e.fragment,
    })),
  };
}

const KIND_ORDER = { candidate: 0, manual: 1, negative: 2, missing: 3, stub: 4 };

api.get('/protocols/:protocolId/checks', (req, res) => {
  const p = getProtocol(req.params.protocolId);
  const P = params();
  let rows = protocolRows(p.id);
  const { kind, criticality, q, decision } = req.query;
  if (kind) rows = rows.filter((r) => String(kind).split(',').includes(r.kind));
  if (criticality === 'critical') rows = rows.filter((r) => r.criticality === CRITICAL);
  if (criticality === 'significant') rows = rows.filter((r) => r.criticality !== CRITICAL);
  if (decision) rows = rows.filter((r) => (r.decision_status || 'PENDING') === decision);
  if (q) {
    const needle = String(q).toLowerCase();
    rows = rows.filter((r) => [r.parameter_code, r.location, P.get(r.parameter_code)?.name, r.pd_value, r.rd_value, r.note]
      .some((v) => v && String(v).toLowerCase().includes(needle)));
  }
  rows.sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]
    || (a.criticality === CRITICAL ? 0 : 1) - (b.criticality === CRITICAL ? 0 : 1)
    || (P.get(a.parameter_code)?.param_id ?? 9999) - (P.get(b.parameter_code)?.param_id ?? 9999)
    || a.location.localeCompare(b.location, 'ru', { numeric: true }));
  const ev = evidenceOf(rows.map((r) => r.id));
  res.json(rows.map((r) => checkView(r, ev, P)));
});

function getCheck(protocolId, checkId) {
  const rows = protocolRows(protocolId).filter((r) => r.id === Number(checkId));
  return need(rows[0], 404, 'CHECK_NOT_FOUND', `Строка ${checkId} не найдена в протоколе ${protocolId}`);
}

api.get('/protocols/:protocolId/checks/:checkId', (req, res) => {
  const p = getProtocol(req.params.protocolId);
  const r = getCheck(p.id, req.params.checkId);
  const P = params();
  const prm = P.get(r.parameter_code) || {};
  const ev = evidenceOf([r.id]);
  const files = new Map(all(`SELECT id, original_name, relative_path, sha256, section, stage FROM files WHERE id IN (${(ev.get(r.id) || []).map(() => '?').join(',') || "''"})`,
    ...(ev.get(r.id) || []).map((e) => e.file_id)).map((f) => [f.id, f]));
  res.json({
    ...checkView(r, ev, P),
    parameter: {
      code: r.parameter_code, name: prm.name, section: prm.section, unit: prm.unit, trigger: prm.trigger_text,
      source_pd: prm.source_pd, source_rd: prm.source_rd, source_id: prm.source_id, in_matrix: Boolean(prm.in_matrix),
    },
    deviation: deviationText(r),
    method: methodOf(r.parameter_code),
    recommendation_template: recommendationTemplate(r),
    files: [...files.values()].map((f) => ({
      file_id: f.id, name: f.original_name || path.basename(f.relative_path), sha256: f.sha256, section: f.section, stage: f.stage,
    })),
  });
});

// ---------- Решения инспектора (ТЗ 9.3) ----------

function assertDecidable(p) {
  const latest = latestProtocol(p.object_id);
  if (latest.id !== p.id) throw new HttpError(409, 'NOT_LATEST_VERSION', `Решения принимаются в последней версии протокола (${latest.version})`);
  if (p.status === 'PROTOCOL_FINALIZED') throw new HttpError(409, 'PROTOCOL_FINALIZED', 'Протокол финализирован: изменение решений невозможно');
}

api.put('/protocols/:protocolId/checks/:checkId/decision', requirePermission('decide'), (req, res) => {
  const p = getProtocol(req.params.protocolId);
  assertDecidable(p);
  const r = getCheck(p.id, req.params.checkId);
  if (r.kind === 'stub' || r.kind === 'missing') {
    throw new HttpError(409, 'NOT_DECIDABLE', 'По этой строке решение не принимается: система её не сравнивала (нет документа или извлечения)');
  }
  const { status, reason_code: reason = null } = req.body;
  const comment = (req.body.comment || '').trim();
  if (status === 'NEGATIVE_VERIFIED') {
    if (!reason || !REASON_CODES[reason]) throw new HttpError(400, 'REASON_REQUIRED', 'Для отклонения нужна причина (reason_code)');
    if (!comment) throw new HttpError(400, 'COMMENT_REQUIRED', 'Для отклонения нужен комментарий');
  }
  const recommendation = status === 'CONFIRMED_VIOLATION' ? (req.body.recommendation || '').trim() || recommendationTemplate(r) : null;
  run(`INSERT INTO decisions(check_id, status, reason_code, comment, recommendation, user_id, decided_at, carried_from_version)
       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
       ON CONFLICT(check_id) DO UPDATE SET status = excluded.status, reason_code = excluded.reason_code, comment = excluded.comment,
         recommendation = excluded.recommendation, user_id = excluded.user_id, decided_at = excluded.decided_at, carried_from_version = NULL,
         carried_note = NULL`,
  r.id, status, status === 'NEGATIVE_VERIFIED' ? reason : null, comment || null, recommendation, req.user.id, now());
  const protocolStatus = refreshProtocolStatus(p.id);
  metrics.counters.decisions_total += 1;
  audit(req, 'DECISION', p.object_id, {
    protocol_version: p.version, parameter_code: r.parameter_code, location: r.location, status, reason_code: reason, comment,
    previous: r.decision_status || 'PENDING',
  });
  const fresh = getCheck(p.id, r.id);
  res.json({ check: checkView(fresh, evidenceOf([r.id]), params()), protocol_status: protocolStatus });
});

api.delete('/protocols/:protocolId/checks/:checkId/decision', requirePermission('decide'), (req, res) => {
  const p = getProtocol(req.params.protocolId);
  assertDecidable(p);
  const r = getCheck(p.id, req.params.checkId);
  run('DELETE FROM decisions WHERE check_id = ?', r.id);
  const protocolStatus = refreshProtocolStatus(p.id);
  audit(req, 'DECISION_RESET', p.object_id, { protocol_version: p.version, parameter_code: r.parameter_code, location: r.location, previous: r.decision_status });
  res.json({ check: checkView(getCheck(p.id, r.id), evidenceOf([r.id]), params()), protocol_status: protocolStatus });
});

// ---------- Финализация ----------

api.post('/protocols/:protocolId/finalize', requirePermission('finalize'), (req, res) => {
  const p = getProtocol(req.params.protocolId);
  assertDecidable(p);
  const pending = protocolRows(p.id).filter((r) => r.kind === 'candidate' && !r.decision_status);
  if (pending.length) {
    throw new HttpError(409, 'CANDIDATES_PENDING', `Финализация невозможна: без решения ${pending.length} кандидат(ов). Подтвердите, отклоните или переведите их в «Требует уточнения».`,
      { pending: pending.map((r) => ({ check_id: r.id, parameter_code: r.parameter_code, location: r.location })) });
  }
  run("UPDATE protocols SET status = 'PROTOCOL_FINALIZED', finalized_at = ?, finalized_by = ? WHERE id = ?", now(), req.user.id, p.id);
  audit(req, 'PROTOCOL_FINALIZE', p.object_id, { protocol_version: p.version, comment: req.body?.comment || null });
  res.json({ protocol_id: p.id, status: 'PROTOCOL_FINALIZED' });
});

api.post('/protocols/:protocolId/unfinalize', requirePermission('unfinalize'), (req, res) => {
  const p = getProtocol(req.params.protocolId);
  if (p.status !== 'PROTOCOL_FINALIZED') throw new HttpError(409, 'NOT_FINALIZED', 'Протокол не финализирован');
  const reason = (req.body.reason || '').trim();
  if (!reason) throw new HttpError(400, 'REASON_REQUIRED', 'Отмена финализации требует указания причины');
  tx(() => {
    run("UPDATE protocols SET status = 'VERIFICATION_COMPLETED', finalized_at = NULL, finalized_by = NULL, sync_status = NULL WHERE id = ?", p.id);
    refreshProtocolStatus(p.id);
  });
  audit(req, 'PROTOCOL_UNFINALIZE', p.object_id, { protocol_version: p.version, reason });
  res.json({ protocol_id: p.id, status: one('SELECT status FROM protocols WHERE id = ?', p.id).status });
});

// ---------- Выгрузка протокола: PDF, DOCX (по образцу Приложения 2), XML, JSON ----------

const MIME = {
  pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xml: 'application/xml; charset=utf-8', json: 'application/json; charset=utf-8',
};

function sendDownload(res, name, mime) {
  res.setHeader('Content-Type', mime);
  res.setHeader('Content-Disposition', `attachment; filename="protocol.${name.split('.').pop()}"; filename*=UTF-8''${encodeURIComponent(name)}`);
}

api.get('/protocols/:protocolId/export', async (req, res) => {
  const p = getProtocol(req.params.protocolId);
  const format = String(req.query.format || 'pdf');
  if (!MIME[format]) throw new HttpError(400, 'BAD_FORMAT', 'Формат: pdf, docx, xml или json');
  const t0 = Date.now();
  const payload = protocolPayload(p.id, req.user);
  const base = `Протокол_${payload.number}`;
  audit(req, 'PROTOCOL_EXPORT', p.object_id, { protocol_version: p.version, format });
  if (format === 'json') {
    sendDownload(res, `${base}.json`, MIME.json);
    const clean = JSON.parse(JSON.stringify(payload, (k, v) => (k === 'pdf_path' ? undefined : v)));
    return res.send(JSON.stringify(clean, null, 2));
  }
  if (format === 'xml') {
    sendDownload(res, `${base}.xml`, MIME.xml);
    return res.send(protocolXml(payload));
  }
  const stamp = `${p.id}_${Date.now()}`;
  const payloadPath = path.join(config.dataDir, 'exports', `${stamp}.json`);
  const outPath = path.join(config.dataDir, 'exports', `${stamp}.${format}`);
  fs.writeFileSync(payloadPath, JSON.stringify(payload), 'utf8');
  try {
    await buildProtocol(payloadPath, format, outPath);
    sendDownload(res, `${base}.${format}`, MIME[format]);
    res.setHeader('X-Generation-Ms', String(Date.now() - t0));
    await new Promise((resolve, reject) => res.sendFile(outPath, (e) => (e ? reject(e) : resolve())));
  } finally {
    fs.rmSync(payloadPath, { force: true });
    fs.rmSync(outPath, { force: true });
  }
});

api.get('/protocols/:protocolId/integrity', (req, res) => {
  const p = getProtocol(req.params.protocolId);
  res.json(JSON.parse(p.integrity || 'null') || { findings: [] });
});

// ---------- Страницы и файлы ----------

function getFile(id) {
  return need(one('SELECT * FROM files WHERE id = ?', id), 404, 'FILE_NOT_FOUND', `Файл ${id} не найден`);
}

api.get('/files/:fileId/pages/:page', async (req, res) => {
  const f = getFile(req.params.fileId);
  if (f.extension !== '.pdf') throw new HttpError(415, 'NOT_PDF', 'Картинка страницы есть только у PDF');
  const page = Number(req.params.page);
  const max = Math.min(Math.max(Number(req.query.max) || 2000, 400), 4000);
  let region = null;
  if (req.query.region) {
    region = String(req.query.region).split(',').map(Number);
    if (region.length !== 4 || region.some((v) => !(v >= 0 && v <= 1)) || region[0] >= region[2] || region[1] >= region[3]) {
      throw new HttpError(400, 'BAD_REGION', 'region: x0,y0,x1,y1 в долях страницы, 0 ≤ x0 < x1 ≤ 1, 0 ≤ y0 < y1 ≤ 1');
    }
    region = region.map((v) => Math.round(v * 1e4) / 1e4);
  }
  const png = await pageImage(f.id, filePath(f), page, max, region);
  res.setHeader('Cache-Control', 'private, max-age=86400');
  res.sendFile(png);
});

api.get('/files/:fileId/download', (req, res) => {
  const f = getFile(req.params.fileId);
  const p = filePath(f);
  if (!fs.existsSync(p)) throw new HttpError(404, 'FILE_NOT_FOUND', 'Файл не найден на диске');
  audit(req, 'FILE_DOWNLOAD', f.object_id, { file_id: f.id });
  const name = f.original_name || path.basename(f.relative_path);
  res.setHeader('Content-Disposition', `inline; filename="file${f.extension}"; filename*=UTF-8''${encodeURIComponent(name)}`);
  res.sendFile(p);
});

// ---------- Передача в ИАИС «РиН» (ТЗ 9.6): только финализированный протокол ----------

api.post('/inspection/:processId', requirePermission('finalize'), async (req, res) => {
  const proc = getProcess(req.params.processId);
  const p = proc.protocol_id ? getProtocol(proc.protocol_id) : null;
  if (!p || p.status !== 'PROTOCOL_FINALIZED') {
    throw new HttpError(409, 'NOT_FINALIZED', 'Передача в ИАИС «РиН» возможна только при статусе протокола PROTOCOL_FINALIZED');
  }
  const payload = protocolPayload(p.id, req.user);
  const confirmed = [...payload.critical, ...payload.significant].filter((f) => f.decision_status === 'CONFIRMED_VIOLATION');
  const pkg = {
    protocol_number: payload.number, protocol_version: p.version, object: payload.object,
    versions: { matrix: p.matrix_version, model: p.model_version, dataset: p.dataset_version },
    input_manifest: JSON.parse(p.files_snapshot || '[]'), input_manifest_sha256: p.input_manifest_hash,
    violations: confirmed.map((f) => ({ ...f, evidence: payload.cards.find((c) => c.finding_id === f.evidence_group_id)?.evidence.map(({ pdf_path, ...e }) => e) })),
  };
  const out = path.join(config.dataDir, 'exports', `rin_${p.object_id}_v${p.version}.json`);
  fs.writeFileSync(out, JSON.stringify(pkg, null, 2), 'utf8');
  let sync = 'PENDING_SYNC';
  let message = 'Адрес ИАИС «РиН» не настроен (RIN_URL): пакет сформирован и сохранён, статус синхронизации PENDING_SYNC';
  if (process.env.RIN_URL) {
    try {
      const r = await fetch(process.env.RIN_URL, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(pkg), signal: AbortSignal.timeout(30000) });
      if (r.ok) { sync = 'SYNCED'; message = 'Передано в ИАИС «РиН»'; } else message = `ИАИС «РиН» ответила ${r.status}; повтор будет выполнен позже`;
    } catch (e) {
      message = `ИАИС «РиН» недоступна (${e.message}); статус PENDING_SYNC, решение инспектора не меняется`;
    }
  }
  run('UPDATE protocols SET sync_status = ? WHERE id = ?', sync, p.id);
  audit(req, 'RIN_SEND', p.object_id, { protocol_version: p.version, sync_status: sync, violations: confirmed.length });
  res.status(sync === 'SYNCED' ? 200 : 202).json({ sync_status: sync, message, violations: confirmed.length, package: pkg });
});

// ---------- Журнал аудита ----------

api.get('/audit', requirePermission('audit'), (req, res) => {
  const where = [];
  const args = [];
  if (req.query.object_id) { where.push('object_id = ?'); args.push(req.query.object_id); }
  if (req.query.action) { where.push('action = ?'); args.push(req.query.action); }
  const limit = Math.min(Number(req.query.limit) || 200, 1000);
  const offset = Number(req.query.offset) || 0;
  const rows = all(`SELECT * FROM audit_log ${where.length ? 'WHERE ' + where.join(' AND ') : ''} ORDER BY id DESC LIMIT ? OFFSET ?`, ...args, limit, offset);
  res.json(rows.map((r) => ({ ...r, action_name: ACTIONS[r.action] || r.action, details: r.details ? JSON.parse(r.details) : null })));
});

// ---------- Данные для дообучения (ТЗ 9.4): только решения CONFIRMED_VIOLATION / NEGATIVE_VERIFIED ----------

api.get('/ml/feedback-dataset', requirePermission('ml_data'), (req, res) => {
  const rows = all(`SELECT c.*, d.status AS gold_label, d.reason_code, d.comment, d.decided_at, d.user_id, p.object_id, p.version, p.status AS protocol_status
                    FROM decisions d JOIN checks c ON c.id = d.check_id JOIN protocols p ON p.id = c.protocol_id
                    WHERE d.status IN ('CONFIRMED_VIOLATION', 'NEGATIVE_VERIFIED')
                      AND p.version = (SELECT MAX(version) FROM protocols p2 WHERE p2.object_id = p.object_id)`);
  const ev = evidenceOf(rows.map((r) => r.id));
  audit(req, 'DATASET_EXPORT', null, { items: rows.length });
  res.setHeader('Content-Type', 'application/x-ndjson; charset=utf-8');
  res.setHeader('Content-Disposition', 'attachment; filename="feedback_dataset_draft.jsonl"');
  res.send(rows.map((r) => JSON.stringify({
    evidence_group_id: r.evidence_group_id, object_id: r.object_id, parameter_code: r.parameter_code, location: r.location,
    gold_label: r.gold_label, reason_code: r.reason_code, expert_id: r.user_id, comment: r.comment, decided_at: r.decided_at,
    protocol_version: r.version, protocol_finalized: r.protocol_status === 'PROTOCOL_FINALIZED',
    dataset_status: 'DRAFT — включение в dataset_version только после проверки куратором данных',
    ml_label: r.violation_label, pd_value: r.pd_value, rd_value: r.rd_value, id_value: r.id_value,
    evidence: (ev.get(r.id) || []).map(({ pdf_path, ...e }) => e),
  })).join('\n') + (rows.length ? '\n' : ''));
});

api.get('/users', requirePermission('users'), (_req, res) => {
  res.json(all('SELECT id, login, full_name, role, is_active, created_at FROM users ORDER BY id').map((u) => ({ ...u, role_name: ROLES[u.role] })));
});
