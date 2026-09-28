// Предметная логика сервиса: виды строк ответа ML, статусы ТЗ, сводка протокола, статус загрузки стадий,
// сценарий проверки, цвет объекта на дашборде, тексты отклонений и рекомендаций для протокола.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config } from './config.js';
import { all, run, one } from './db.js';

const REFERENCE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'reference');

export const CRITICAL = 'Критическое (приостановка работ)';
export const SIGNIFICANT = 'Существенное (предписание)';

export const STAGES = { PD: 'ПД', RD: 'РД', ID: 'ИД' };
export const STAGE_NAMES = { PD: 'Проектная документация', RD: 'Рабочая документация', ID: 'Исполнительная документация' };

// Причины отклонения кандидата (ТЗ 9.3: reason_code обязателен)
export const REASON_CODES = {
  WRONG_REVISION: 'Неверно выбрана актуальная редакция',
  APPROVED_CHANGE: 'Согласованное изменение проектной документации',
  OCR_ERROR: 'Ошибка распознавания (OCR)',
  BINDING_ERROR: 'Ошибка привязки (не то помещение, элемент или лист)',
  EXTRACTION_ERROR: 'Значение извлечено неверно',
  NOT_APPLICABLE: 'Параметр неприменим к объекту',
  OTHER: 'Другое (см. комментарий)',
};

export const DECISIONS = {
  CONFIRMED_VIOLATION: 'Нарушение подтверждено',
  NEGATIVE_VERIFIED: 'Отклонено',
  CLARIFICATION_REQUIRED: 'Требует уточнения',
};

export const KIND_NAMES = {
  candidate: 'Кандидат в нарушения',
  negative: 'Проверено, нарушений нет',
  missing: 'Не хватает документа',
  manual: 'Нужна ручная проверка',
  stub: 'Не проверялось системой',
};

// Строка ответа ML → вид строки для интерфейса и статус ТЗ 9.2
export function kindOf(c) {
  switch (c.violation_label) {
    case 'VIOLATION_PRESENT': return 'candidate';
    case 'NO_VIOLATION': return 'negative';
    case 'MISSING_DOCUMENT': return 'missing';
    default: {
      const empty = !(c.evidence?.length) && c.pd_value == null && c.rd_value == null && c.id_value == null;
      return empty ? 'stub' : 'manual';
    }
  }
}

export const FINDING_STATUS = {
  candidate: 'CANDIDATE', negative: 'NEGATIVE_VERIFIED', missing: 'MISSING_EVIDENCE', manual: 'NOT_COMPARABLE', stub: 'NOT_COMPARABLE',
};

// Уровень риска — только очерёдность экспертной проверки (ТЗ 9.2), не статус нарушения
export function reviewPriority(kind, criticality) {
  if (kind === 'candidate') return criticality === CRITICAL ? 'HIGH' : 'MEDIUM';
  if (kind === 'manual') return criticality === CRITICAL ? 'MEDIUM' : 'LOW';
  return 'LOW';
}

export function evidenceGroupId(objectId, code, location) {
  return 'EG-' + crypto.createHash('sha1').update(`${objectId}\u0000${code}\u0000${location}`).digest('hex').slice(0, 12);
}

// ---------- Матрица параметров ----------

export const FREE_HEATING = {
  parameter_id: 1000, parameter_code: 'FREE-HEATING-001', pd_section: 'Свободный поиск', parameter_name: 'Тёплые полы (свободный поиск, вне матрицы)',
  unit: '', source_pd: 'Чертежи ОВ (ПД)', source_rd: 'Чертежи ОВ (РД)', source_id: '',
  trigger: 'Тёплый пол, предусмотренный в ПД для помещения, отсутствует в РД.', criticality: SIGNIFICANT,
};

export function catalogPath() {
  const fromPackage = config.packageData && path.join(config.packageData, 'parameter_catalog_132.jsonl');
  return fromPackage && fs.existsSync(fromPackage) ? fromPackage : path.join(REFERENCE, 'parameter_catalog_132.jsonl');
}
export function schemaPath() {
  const fromPackage = config.packageData && path.join(config.packageData, 'submission_schema.json');
  return fromPackage && fs.existsSync(fromPackage) ? fromPackage : path.join(REFERENCE, 'submission_schema.json');
}

export function readJsonl(p) {
  return fs.readFileSync(p, 'utf8').split(/\r?\n/).filter((l) => l.trim()).map((l) => JSON.parse(l));
}

export function loadParams() {
  const rows = [...readJsonl(catalogPath()), FREE_HEATING];
  for (const r of rows) {
    run(`INSERT INTO params(code, param_id, section, name, unit, source_pd, source_rd, source_id, trigger_text, criticality, in_matrix)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(code) DO UPDATE SET param_id = excluded.param_id, section = excluded.section, name = excluded.name,
           unit = excluded.unit, source_pd = excluded.source_pd, source_rd = excluded.source_rd, source_id = excluded.source_id,
           trigger_text = excluded.trigger_text, criticality = excluded.criticality, in_matrix = excluded.in_matrix`,
    r.parameter_code, r.parameter_id, r.pd_section, r.parameter_name, r.unit ?? '', r.source_pd ?? '', r.source_rd ?? '',
    r.source_id ?? '', r.trigger ?? '', r.criticality ?? '', r === FREE_HEATING ? 0 : 1);
  }
  return rows.length;
}

let _params = null;
export function params() {
  if (!_params) _params = new Map(all('SELECT * FROM params').map((p) => [p.code, p]));
  return _params;
}

// «Раздел 5. ИОС1» → «ИОС1»
export function shortSection(section) {
  const m = /^Раздел\s+\d+\.\s*(.+)$/.exec(section || '');
  return m ? m[1] : section || '';
}

export function matrixVersion() {
  const hash = crypto.createHash('sha256').update(fs.readFileSync(catalogPath())).digest('hex');
  return `Матрица 132 параметров, ред. 1.1 (sha256 ${hash.slice(0, 12)})`;
}

// ---------- Статус загрузки стадий, сценарий ----------

// ТЗ 9.1: PD_UPLOADED / PD_PARTIAL / PD_MISSING… Ожидаемое число файлов системе не известно, поэтому «частично» —
// по находкам проверки комплектности: в РД нет комплектов разделов, которые есть в ПД; файлы стадии не читаются.
export function stageStatus(files, integrity) {
  const findings = integrity?.findings || [];
  const byStage = { PD: [], RD: [], ID: [] };
  for (const f of files) {
    if (f.stage === 'RD_ID_MIXED') { byStage.RD.push(f); byStage.ID.push(f); } else if (byStage[f.stage]) byStage[f.stage].push(f);
  }
  return Object.keys(byStage).map((stage) => {
    const list = byStage[stage];
    const comments = [];
    let partial = false;
    const ids = new Set(list.map((f) => f.id ?? f.file_id));
    for (const fd of findings) {
      const inStage = (fd.file_ids || []).filter((id) => ids.has(id));
      if (fd.type === 'DISCIPLINE_MISSING_IN_RD' && stage === 'RD') {
        partial = true;
        comments.push(`нет комплектов: ${(fd.disciplines || []).join(', ')}`);
      } else if (fd.type === 'UNREADABLE_OR_EMPTY_SOURCE_FILE' && inStage.length) {
        partial = true;
        comments.push(`не читается файлов: ${inStage.length}`);
      } else if (fd.type === 'PDF_WITHOUT_MACHINE_READABLE_TEXT' && (fd.stage === stage || inStage.length)) {
        comments.push(`сканов без текстового слоя: ${fd.count}`);
      }
    }
    let status = `${stage}_UPLOADED`;
    if (!list.length) status = `${stage}_MISSING`;
    else if (partial) status = `${stage}_PARTIAL`;
    const text = { UPLOADED: 'Загружена', PARTIAL: 'Частично', MISSING: 'Отсутствует' }[status.split('_')[1]];
    if (!list.length) comments.push('файлы стадии не загружены');
    else if (!comments.length) comments.push('все файлы загружены');
    return { stage, stage_name: STAGE_NAMES[stage], status, status_text: text, files: list.length, comment: comments.join('; ') };
  });
}

export function scenario(stages) {
  const present = stages.filter((s) => s.files > 0).map((s) => s.stage);
  const key = present.join('+');
  const map = { 'PD+RD+ID': 'FULL', 'PD+RD': 'PD_RD_ONLY', 'PD+ID': 'PD_ID_ONLY', 'RD+ID': 'RD_ID_ONLY' };
  if (map[key]) return map[key];
  return present.length === 1 ? 'SINGLE_ONLY' : 'NO_DOCUMENTS';
}

export const SCENARIO_NAMES = {
  FULL: 'Полная проверка ПД – РД – ИД',
  PD_RD_ONLY: 'Сверка ПД – РД (ИД не загружена)',
  PD_ID_ONLY: 'Сверка ПД – ИД (РД не загружена)',
  RD_ID_ONLY: 'Сверка РД – ИД (ПД не загружена)',
  SINGLE_ONLY: 'Загружена одна стадия — сверка невозможна',
  NO_DOCUMENTS: 'Документы не загружены',
};

// ---------- Сводка протокола ----------

export function protocolRows(protocolId) {
  return all(`SELECT c.*, d.status AS decision_status, d.reason_code, d.comment AS decision_comment, d.recommendation,
                     d.decided_at, d.carried_from_version, d.carried_note, u.full_name AS decided_by
              FROM checks c LEFT JOIN decisions d ON d.check_id = c.id LEFT JOIN users u ON u.id = d.user_id
              WHERE c.protocol_id = ?`, protocolId);
}

// Статус параметра матрицы по всем его строкам: нарушение > проверено > ручная проверка > нет документа > не проверялось
const PARAM_RANK = { candidate: 5, negative: 4, manual: 3, missing: 2, stub: 1 };

export function summarize(rows) {
  const P = params();
  const perParam = new Map();
  for (const r of rows) {
    const cur = perParam.get(r.parameter_code);
    if (!cur || PARAM_RANK[r.kind] > PARAM_RANK[cur]) perParam.set(r.parameter_code, r.kind);
  }
  const matrixCodes = [...P.values()].filter((p) => p.in_matrix).map((p) => p.code);
  const total = matrixCodes.length || 132;
  const byParam = { candidate: 0, negative: 0, manual: 0, missing: 0, stub: 0 };
  for (const code of matrixCodes) byParam[perParam.get(code) || 'stub'] += 1;

  const byKind = { candidate: 0, negative: 0, manual: 0, missing: 0, stub: 0 };
  for (const r of rows) byKind[r.kind] += 1;
  const cand = rows.filter((r) => r.kind === 'candidate');
  const decisions = { PENDING: 0, CONFIRMED_VIOLATION: 0, NEGATIVE_VERIFIED: 0, CLARIFICATION_REQUIRED: 0 };
  for (const r of cand) decisions[r.decision_status || 'PENDING'] += 1;
  const manualDecided = rows.filter((r) => r.kind === 'manual' && r.decision_status).length;
  const confirmedAll = rows.filter((r) => r.decision_status === 'CONFIRMED_VIOLATION');
  return {
    parameters_total: total,
    parameters: {
      checked: byParam.candidate + byParam.negative,      // значения сверены на стадиях
      with_candidates: byParam.candidate,
      no_violation: byParam.negative,
      manual: byParam.manual,
      missing: byParam.missing,
      not_checked: byParam.stub,
    },
    rows: { total: rows.length, ...byKind },
    candidates: {
      total: cand.length,
      critical: cand.filter((r) => r.criticality === CRITICAL).length,
      significant: cand.filter((r) => r.criticality !== CRITICAL).length,
      decisions,
    },
    manual_decided: manualDecided,
    confirmed: {
      total: confirmedAll.length,
      critical: confirmedAll.filter((r) => r.criticality === CRITICAL).length,
      significant: confirmedAll.filter((r) => r.criticality !== CRITICAL).length,
    },
    suspicions: byKind.manual,
  };
}

// Статус протокола по решениям (ТЗ 9.3). Финализированный не пересчитывается.
export function computeProtocolStatus(protocol, summary) {
  if (protocol.status === 'PROTOCOL_FINALIZED') return protocol.status;
  const d = summary.candidates.decisions;
  if (d.PENDING === 0) return 'VERIFICATION_COMPLETED';
  const anyDecision = d.CONFIRMED_VIOLATION + d.NEGATIVE_VERIFIED + d.CLARIFICATION_REQUIRED + summary.manual_decided;
  return anyDecision ? 'VERIFYING' : 'READY';
}

export function refreshProtocolStatus(protocolId) {
  const p = one('SELECT * FROM protocols WHERE id = ?', protocolId);
  const status = computeProtocolStatus(p, summarize(protocolRows(protocolId)));
  if (status !== p.status) run('UPDATE protocols SET status = ? WHERE id = ?', status, protocolId);
  return status;
}

export const PROTOCOL_STATUS_NAMES = {
  READY: 'Ожидает верификации (дозагрузка возможна)',
  VERIFYING: 'Идёт верификация (дозагрузка возможна)',
  VERIFICATION_COMPLETED: 'Верификация завершена, протокол не финализирован',
  PROTOCOL_FINALIZED: 'Протокол финализирован',
};

// Статус процесса по таблице ТЗ 9.1: PENDING, PARSING, READY, VERIFYING, COMPLETED, FINALIZED (+ FAILED)
export function processStatus(proc, protocol) {
  if (proc.status !== 'DONE') return proc.status;
  const map = { READY: 'READY', VERIFYING: 'VERIFYING', VERIFICATION_COMPLETED: 'COMPLETED', PROTOCOL_FINALIZED: 'FINALIZED' };
  return protocol ? map[protocol.status] || 'READY' : 'READY';
}

// Цвет объекта на дашборде: красный — есть подтверждённые нарушения; жёлтый — кандидаты ждут решения или
// уточнения; зелёный — все кандидаты обработаны, подтверждённых нарушений нет; серый — протокола ещё нет.
export function indicator(summary) {
  if (!summary) return 'gray';
  if (summary.confirmed.total > 0) return 'red';
  const d = summary.candidates.decisions;
  if (d.PENDING > 0 || d.CLARIFICATION_REQUIRED > 0) return 'yellow';
  return 'green';
}

// ---------- Тексты для протокола ----------

const val = (v) => (v == null || v === '' ? '—' : v);

export function deviationText(r) {
  const code = r.parameter_code;
  if (r.note) return r.note;
  if (code === 'KR-055') return `Понижение класса бетона: ${val(r.pd_value)} → ${val(r.rd_value)}`;
  if (code === 'KR-057') return `Понижение класса арматуры: ${val(r.pd_value)} → ${val(r.rd_value)}`;
  if (/^KR-05[89]|^KR-06[01]/.test(code)) return `Уменьшение размеров: ${val(r.pd_value)} → ${val(r.rd_value)}`;
  if (code === 'IOS4-078' || code === 'IOS4-079') return 'Системы вентиляции ПД у помещения не совпадают с РД';
  if (code === 'FREE-HEATING-001') return 'Тёплый пол из ПД в РД не найден';
  return `${val(r.pd_value)} → ${val(r.rd_value)}`;
}

// Рекомендация по подтверждённому нарушению (раздел 7 протокола); инспектор может её отредактировать
export function recommendationTemplate(r) {
  const p = params().get(r.parameter_code) || {};
  const loc = r.location || 'объект';
  const pd = val(r.pd_value);
  const rd = val(r.rd_value);
  const code = r.parameter_code;
  if (code === 'KR-055') return `Представить перерасчёт несущей способности конструкций (${loc}) при классе бетона ${rd} вместо ${pd}, согласованный с проектировщиком и подтверждённый испытаниями, либо привести РД в соответствие с ПД.`;
  if (code === 'KR-057') return `Представить обоснование применения арматуры ${rd} вместо ${pd} (${loc}) с перерасчётом армирования, согласованным с проектировщиком; при отсутствии обоснования — разработать мероприятия по усилению.`;
  if (code === 'KR-056') return `Представить обоснование замены стали (${loc}: ПД ${pd}, РД ${rd}) с перерасчётом конструкций, согласованным с проектировщиком.`;
  if (/^KR-05[89]|^KR-06[01]/.test(code)) return `Представить перерасчёт конструкций (${loc}) при изменённых размерах (ПД ${pd}, РД ${rd}) либо привести РД в соответствие с ПД.`;
  if (code === 'IOS4-078' || code === 'IOS4-079') return `Привести РД раздела ОВ для помещения ${loc} в соответствие с ПД (${pd}) либо представить согласованное изменение ПД с расчётом воздухообмена.`;
  if (code === 'FREE-HEATING-001') return `Предусмотреть в РД тёплый пол в помещении ${loc}, как в ПД, либо представить согласованное изменение ПД.`;
  if (code === 'PZ-003') return `Представить согласованное изменение ПД по составу и площадям помещений (${loc}) либо привести РД в соответствие с утверждённым проектом.`;
  if (code === 'IOS3-075') return `Привести материал трубопроводов канализации системы ${loc} в соответствие с ПД (${pd}) либо представить согласованное изменение ПД.`;
  if (code === 'PPM-109') return `Предусмотреть для систем противопожарной защиты огнестойкие кабели по ПД (${pd}); представить сертификаты и протоколы испытаний.`;
  if (code === 'PPM-103') return `Заменить двери на сертифицированные с пределом огнестойкости не ниже, чем в ПД (${pd}); представить сертификаты и акты монтажа.`;
  if (code === 'ZU-130') return `Привести тип светильников в соответствие с ПД (${pd}) либо представить согласованное изменение ПД с расчётом энергоэффективности.`;
  if (code === 'PZ-014') return 'Представить изменение технических условий на технологическое присоединение либо привести расчётную мощность в соответствие с ПД.';
  return `Привести РД в соответствие с ПД по параметру «${p.name || code}» (${loc}: ПД ${pd}, РД ${rd}) либо представить согласованное изменение проектной документации.`;
}

// «Метод» для раздела 6 протокола (у строк ответа ML такого поля нет — берётся по коду параметра)
export function methodOf(code) {
  if (/^KR-05[5789]|^KR-06[01]|^PZ-009/.test(code)) return 'Извлечение из текста и спецификаций КР';
  if (code === 'KR-056') return 'Марки стали из текста КР / КМ (предел текучести)';
  if (code === 'IOS3-075') return 'Материал труб из текста ИОС3 / ВК';
  if (code === 'PPM-109' || code === 'IOS1-069') return 'Исполнение кабелей из текста ИОС1 / ЭОМ';
  if (code === 'ZU-130') return 'Источники света из текста ИОС1 / ЭОМ';
  if (code === 'PZ-014') return 'Мощность объекта из текста ИОС1 / ЭОМ';
  if (code === 'PPM-103') return 'Пределы огнестойкости дверей (текст и планы АР)';
  if (code === 'PZ-003') return 'Разбор экспликаций помещений ПД и РД';
  if (code === 'PZ-022' || code === 'PZ-023') return 'Пожарные характеристики из текста ПЗ, АР, КР';
  if (/^IOS4-07[89]|^FREE-HEATING/.test(code)) return 'Чертежи ОВ: марки систем → помещения';
  return 'Сопоставление значений ПД / РД / ИД';
}

// Системный комментарий ИИ к решению инспектора (формулировки — из ТЗ 9.4)
export function aiComment(r) {
  if (r.decision_status === 'NEGATIVE_VERIFIED') {
    return `Результат инспектора: NEGATIVE_VERIFIED. Причина: ${r.reason_code}. Запись включена в черновик следующей версии набора данных; её использование для обучения допускается только после проверки куратором данных и выпуска dataset_version.`;
  }
  if (r.decision_status === 'CLARIFICATION_REQUIRED') {
    return 'Статус: CLARIFICATION_REQUIRED. Показаны точные страницы и доказательные фрагменты. До повторного решения инспектора запись не включается в GOLD и не передаётся во внешнюю систему.';
  }
  if (r.decision_status === 'CONFIRMED_VIOLATION') {
    return 'Результат инспектора: CONFIRMED_VIOLATION. Запись сохранена как положительный GOLD-кандидат; передача наружу — только после финализации протокола.';
  }
  return null;
}

export function decisionText(r) {
  if (!r.decision_status) return 'Ожидает решения';
  if (r.decision_status === 'NEGATIVE_VERIFIED') return `Отклонено: ${REASON_CODES[r.reason_code] || r.reason_code}`;
  return DECISIONS[r.decision_status];
}
