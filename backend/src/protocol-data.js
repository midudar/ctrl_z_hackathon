// Данные протокола по образцу Приложения 2 к ТЗ (обязательный образец, ТЗ 9.2): 7 разделов + приложения
// с карточками доказательств (ТЗ 9.2, п. 4). Один набор данных — для PDF и DOCX (Python, ml/protocol.py),
// XML и JSON (здесь).
import path from 'node:path';
import { all, one } from './db.js';
import {
  params, protocolRows, summarize, stageStatus, shortSection, deviationText, recommendationTemplate, methodOf,
  aiComment, decisionText, REASON_CODES, SCENARIO_NAMES, PROTOCOL_STATUS_NAMES, CRITICAL, KIND_NAMES,
} from './domain.js';
import { filePath } from './files.js';

const dash = (v) => (v == null || v === '' ? '—' : String(v));
const pct = (n, total) => `${((100 * n) / (total || 1)).toFixed(1).replace('.', ',')}%`;
const ruDate = (iso) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '—');
const ruDateTime = (iso) => (iso ? new Date(iso).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) : '—');

const STATUS_HEADLINE = {
  READY: 'ОЖИДАЕТ ВЕРИФИКАЦИИ (дозагрузка возможна)',
  VERIFYING: 'ИДЁТ ВЕРИФИКАЦИЯ (дозагрузка возможна)',
  VERIFICATION_COMPLETED: 'ВЕРИФИКАЦИЯ ЗАВЕРШЕНА, ПРОТОКОЛ НЕ ФИНАЛИЗИРОВАН',
  PROTOCOL_FINALIZED: 'ПРОТОКОЛ ФИНАЛИЗИРОВАН',
};

function sortRows(rows) {
  const P = params();
  return rows.sort((a, b) => (P.get(a.parameter_code)?.param_id ?? 9999) - (P.get(b.parameter_code)?.param_id ?? 9999)
    || a.location.localeCompare(b.location, 'ru', { numeric: true }));
}

// «в РД нет комплекта ОВ» → «РД: комплект ОВ»
function missingDoc(r) {
  const m = /^в\s+(ПД|РД|ИД)\s+нет\s+(.+)$/i.exec(r.note || '');
  if (m) return `${m[1]}: нет ${m[2]}`;
  const st = { RD_MISSING: 'РД', PD_MISSING: 'ПД', ID_MISSING: 'ИД' }[r.protocol_status];
  return r.note || (st ? `${st}: документ не загружен` : 'документ не найден');
}

export function evidenceOf(checkIds) {
  if (!checkIds.length) return new Map();
  const rows = all(`SELECT e.*, f.original_name, f.relative_path, f.sha256, f.section, f.stage AS file_stage
                    FROM evidence_fragments e LEFT JOIN files f ON f.id = e.file_id
                    WHERE e.check_id IN (${checkIds.map(() => '?').join(',')}) ORDER BY e.check_id, e.ord`, ...checkIds);
  const map = new Map();
  for (const e of rows) {
    let pdf = null;
    try { pdf = e.relative_path ? filePath({ relative_path: e.relative_path }) : null; } catch { pdf = null; }
    const item = {
      stage: e.stage, file_id: e.file_id, page: e.page, bbox_norm: e.bbox_norm ? JSON.parse(e.bbox_norm) : null,
      bbox_source: e.bbox_source, fragment: e.fragment,
      file_name: e.original_name || (e.relative_path ? path.basename(e.relative_path) : e.file_id),
      sha256: e.sha256, pdf_path: pdf,
    };
    if (!map.has(e.check_id)) map.set(e.check_id, []);
    map.get(e.check_id).push(item);
  }
  return map;
}

export function protocolPayload(protocolId, user) {
  const p = one('SELECT * FROM protocols WHERE id = ?', protocolId);
  const obj = one('SELECT * FROM objects WHERE id = ?', p.object_id);
  const finalizer = p.finalized_by ? one('SELECT full_name FROM users WHERE id = ?', p.finalized_by) : null;
  const P = params();
  const rows = sortRows(protocolRows(protocolId));
  const s = summarize(rows);
  const files = JSON.parse(p.files_snapshot || '[]').map((f) => ({ ...f, id: f.file_id }));
  const integrity = JSON.parse(p.integrity || 'null');
  const stages = stageStatus(files, integrity);
  const T = s.parameters_total;
  const info = (r) => {
    const prm = P.get(r.parameter_code) || {};
    return { code: r.parameter_code, name: prm.name || r.parameter_code, section: shortSection(prm.section), location: r.location };
  };

  const candidates = rows.filter((r) => r.kind === 'candidate');
  const manual = rows.filter((r) => r.kind === 'manual');
  const ev = evidenceOf([...candidates, ...manual, ...rows.filter((r) => r.kind === 'negative')].map((r) => r.id));

  let no = 0;
  const finding = (r) => ({
    no: ++no, ...info(r), check_id: r.id, evidence_group_id: r.evidence_group_id, criticality: r.criticality,
    pd: dash(r.pd_value), rd: dash(r.rd_value), id: dash(r.id_value), deviation: deviationText(r),
    decision_status: r.decision_status || 'PENDING', decision: decisionText(r), reason_code: r.reason_code || null,
    reason: r.reason_code ? REASON_CODES[r.reason_code] || r.reason_code : null, comment: r.decision_comment || null,
    decided_by: r.decided_by || null, decided_at: r.decided_at ? ruDateTime(r.decided_at) : null,
    recommendation: r.recommendation || recommendationTemplate(r), review_priority: r.review_priority,
  });
  const critical = candidates.filter((r) => r.criticality === CRITICAL).map(finding);
  const significant = candidates.filter((r) => r.criticality !== CRITICAL).map(finding);

  const confirmed = [...critical, ...significant].filter((f) => f.decision_status === 'CONFIRMED_VIOLATION');
  const resolution = (list) => list.map((f, i) => ({
    no: i + 1, work: `${f.name} — ${f.location || 'объект'} (${f.code})`, recommendation: f.recommendation,
  }));

  const d = s.candidates.decisions;
  const summaryRows = [
    ['Всего параметров в Матрице', T, '100%'],
    ['Проверено (значения сверены на стадиях)', s.parameters.checked, pct(s.parameters.checked, T)],
    ['Не проверено из-за отсутствия документов', s.parameters.missing, pct(s.parameters.missing, T)],
    ['Сравнить не удалось — нужна ручная проверка', s.parameters.manual, pct(s.parameters.manual, T)],
    ['Не проверялось системой (извлечение не реализовано)', s.parameters.not_checked, pct(s.parameters.not_checked, T)],
    ['Выявлено кандидатов в нарушения (всего)', s.candidates.total, pct(s.candidates.total, T)],
    ['─ Критических (приостановка)', s.candidates.critical, pct(s.candidates.critical, T)],
    ['─ Существенных (предписание)', s.candidates.significant, pct(s.candidates.significant, T)],
    ['Подтверждено инспектором', d.CONFIRMED_VIOLATION, pct(d.CONFIRMED_VIOLATION, T)],
    ['Отклонено инспектором', d.NEGATIVE_VERIFIED, pct(d.NEGATIVE_VERIFIED, T)],
    ['Требует уточнения', d.CLARIFICATION_REQUIRED, pct(d.CLARIFICATION_REQUIRED, T)],
    ['Ожидает решения инспектора', d.PENDING, pct(d.PENDING, T)],
    ['Подозрений ИИ (требуют ручной проверки)', s.suspicions, pct(s.suspicions, T)],
  ].map(([label, count, share]) => ({ label, count, share }));

  const missing = rows.filter((r) => r.kind === 'missing').map((r, i) => ({ no: i + 1, ...info(r), missing: missingDoc(r) }));

  const suspicions = manual.map((r, i) => ({
    no: i + 1, ...info(r), method: methodOf(r.parameter_code),
    description: `${info(r).name} (${r.parameter_code})${r.location ? `, ${r.location}` : ''}${r.note ? `: ${r.note}` : ''}`,
    pd: dash(r.pd_value), rd: dash(r.rd_value), id: dash(r.id_value),
    decision: decisionText(r), reason: r.reason_code ? REASON_CODES[r.reason_code] || r.reason_code : '—',
    comment: r.decision_comment || null, ai_comment: aiComment(r) || '—',
  }));

  const negatives = rows.filter((r) => r.kind === 'negative').map((r, i) => ({
    no: i + 1, ...info(r), pd: dash(r.pd_value), rd: dash(r.rd_value), id: dash(r.id_value),
    evidence: (ev.get(r.id) || []).map((e) => `${e.stage} ${e.file_id} с. ${e.page}`).join('; '),
  }));

  const cards = candidates.map((r) => {
    const f = [...critical, ...significant].find((x) => x.check_id === r.id);
    const prm = P.get(r.parameter_code) || {};
    return {
      finding_no: f.no, finding_id: r.evidence_group_id, ...info(r), criticality: r.criticality, review_priority: r.review_priority,
      expected: dash(r.pd_value), actual_rd: dash(r.rd_value), actual_id: dash(r.id_value),
      trigger: prm.trigger_text || '', note: r.note || null, decision: f.decision, reason: f.reason, comment: f.comment,
      decided_by: f.decided_by, decided_at: f.decided_at, ai_comment: aiComment(r),
      evidence: ev.get(r.id) || [],
    };
  });

  const stageName = { PD: 'ПД', RD: 'РД', ID: 'ИД' };
  return {
    number: `${(p.created_at || '').slice(0, 10)}-${p.object_id.replace(/^OBJ-/, '')}-v${p.version}`,
    title: 'ПРОТОКОЛ АВТОМАТИЗИРОВАННОЙ СВЕРКИ',
    object: {
      id: obj.id, name: obj.name, address: dash(obj.address), case_number: dash(obj.case_number), developer: dash(obj.developer),
      contractor: dash(obj.contractor), permit_number: dash(obj.permit_number),
    },
    protocol: {
      id: p.id, version: p.version, version_text: `${p.version} (${p.status === 'PROTOCOL_FINALIZED' ? 'окончательная' : 'предварительная'})`,
      status: p.status, status_text: PROTOCOL_STATUS_NAMES[p.status], headline: STATUS_HEADLINE[p.status],
      created_at: ruDate(p.created_at), finalized_at: p.finalized_at ? ruDateTime(p.finalized_at) : null,
      finalized_by: finalizer?.full_name || null, scenario: p.scenario, scenario_text: SCENARIO_NAMES[p.scenario] || p.scenario,
      matrix_version: p.matrix_version, model_version: p.model_version, dataset_version: p.dataset_version,
      input_manifest_hash: p.input_manifest_hash,
    },
    generated_at: ruDateTime(new Date().toISOString()),
    generated_by: user?.full_name || null,
    stages: stages.map((st) => ({ ...st, stage_short: stageName[st.stage] })),
    summary: summaryRows,
    summary_raw: s,
    missing,
    critical,
    significant,
    suspicions,
    resolution: { critical: resolution(confirmed.filter((f) => f.criticality === CRITICAL)), significant: resolution(confirmed.filter((f) => f.criticality !== CRITICAL)) },
    negatives,
    cards,
    integrity: (integrity?.findings || []).map((x) => ({ severity: x.severity, type: x.type, title: x.title, count: x.count })),
    kinds: KIND_NAMES,
  };
}

// ---------- XML ----------

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const attrs = (o) => Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => ` ${k}="${esc(v)}"`).join('');
const el = (name, a, children) => (children == null || children === ''
  ? `<${name}${attrs(a)}/>` : `<${name}${attrs(a)}>${children}</${name}>`);
const text = (name, v) => (v == null ? '' : `<${name}>${esc(v)}</${name}>`);

export function protocolXml(pl) {
  const finding = (f) => el('finding', {
    no: f.no, evidence_group_id: f.evidence_group_id, parameter_code: f.code, location: f.location, criticality: f.criticality,
    review_priority: f.review_priority,
  }, [
    text('parameter', f.name), text('section', f.section), text('pd', f.pd), text('rd', f.rd), text('id', f.id), text('deviation', f.deviation),
    el('decision', { status: f.decision_status, reason_code: f.reason_code, user: f.decided_by, at: f.decided_at }, esc(f.comment || '')),
    f.decision_status === 'CONFIRMED_VIOLATION' ? text('recommendation', f.recommendation) : '',
    el('evidence_list', {}, (pl.cards.find((c) => c.finding_id === f.evidence_group_id)?.evidence || []).map((e) => el('evidence', {
      stage: e.stage, file_id: e.file_id, file_name: e.file_name, sha256: e.sha256, page: e.page,
      bbox_norm: e.bbox_norm ? e.bbox_norm.join(' ') : null, bbox_source: e.bbox_source,
    }, esc(e.fragment || ''))).join('')),
  ].join(''));
  const body = [
    el('object', { id: pl.object.id }, [text('name', pl.object.name), text('address', pl.object.address), text('case_number', pl.object.case_number),
      text('developer', pl.object.developer), text('contractor', pl.object.contractor)].join('')),
    el('versions', { matrix: pl.protocol.matrix_version, model: pl.protocol.model_version, dataset: pl.protocol.dataset_version, input_manifest_sha256: pl.protocol.input_manifest_hash }),
    el('check_type', { scenario: pl.protocol.scenario }, esc(pl.protocol.scenario_text)),
    el('section', { no: 1, title: 'Статус загрузки документов' }, pl.stages.map((s) => el('stage', { code: s.stage, status: s.status, files: s.files }, esc(s.comment))).join('')),
    el('section', { no: 2, title: 'Сводная статистика' }, pl.summary.map((r) => el('row', { label: r.label, count: r.count, share: r.share })).join('')),
    el('section', { no: 3, title: 'Параметры, не проверенные из-за отсутствия документов' }, pl.missing.map((m) => el('parameter', { no: m.no, code: m.code, section: m.section }, text('name', m.name) + text('missing', m.missing))).join('')),
    el('section', { no: 4, title: 'Критические нарушения' }, pl.critical.map(finding).join('')),
    el('section', { no: 5, title: 'Существенные нарушения' }, pl.significant.map(finding).join('')),
    el('section', { no: 6, title: 'Подозрения ИИ' }, pl.suspicions.map((s) => el('suspicion', { no: s.no, parameter_code: s.code, location: s.location, method: s.method },
      [text('description', s.description), text('pd', s.pd), text('rd', s.rd), text('id', s.id), text('decision', s.decision), text('reason', s.reason), text('ai_comment', s.ai_comment)].join(''))).join('')),
    el('section', { no: 7, title: 'Резолютивная часть' }, [
      el('critical', {}, pl.resolution.critical.map((r) => el('item', { no: r.no }, text('work', r.work) + text('recommendation', r.recommendation))).join('')),
      el('significant', {}, pl.resolution.significant.map((r) => el('item', { no: r.no }, text('work', r.work) + text('recommendation', r.recommendation))).join('')),
    ].join('')),
    el('negative_verified', {}, pl.negatives.map((n) => el('check', { no: n.no, parameter_code: n.code, location: n.location },
      text('pd', n.pd) + text('rd', n.rd) + text('id', n.id) + text('evidence', n.evidence))).join('')),
    el('document_integrity', {}, pl.integrity.map((x) => el('finding', { severity: x.severity, type: x.type, count: x.count }, esc(x.title))).join('')),
  ].join('');
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + el('protocol', {
    number: pl.number, version: pl.protocol.version, status: pl.protocol.status, created: pl.protocol.created_at,
    finalized: pl.protocol.finalized_at, generated: pl.generated_at,
  }, body) + '\n';
}
