// Интеграционные тесты API: запускают приложение на временной базе, без ML и без пакета организаторов.
//   npm test
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'inspector-test-'));
Object.assign(process.env, {
  DATA_DIR: tmp, SEED: '0', PACKAGE_DATA: path.join(tmp, 'нет'), PACKAGE_DOCS: path.join(tmp, 'нет'), LOG_LEVEL: 'ERROR',
  AMQP_URL: '', ML_HTTP_URL: '',
});

const { createApp } = await import('../src/app.js');
const { ensureDefaultUsers } = await import('../src/auth.js');
const { loadParams } = await import('../src/domain.js');
const { recordImport } = await import('../src/queue.js');
const { run, now } = await import('../src/db.js');

let server;
let base;
const tokens = {};

async function call(method, url, { token, body } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(base + url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  let data = text;
  try { data = JSON.parse(text); } catch { /* не JSON */ }
  return { status: res.status, data };
}

const SUBMISSION = {
  object_id: 'OBJ-TEST',
  checks: [
    { parameter_code: 'KR-055', location: 'Фундаментная плита', violation_label: 'VIOLATION_PRESENT', protocol_status: 'CRITICAL',
      criticality: 'Критическое (приостановка работ)', pd_value: 'B40', rd_value: 'B30', id_value: null,
      evidence: [{ stage: 'PD', file_id: 'T001', pdf_page_number: 3, bbox_norm: [0.1, 0.2, 0.5, 0.25] },
        { stage: 'RD', file_id: 'T002', pdf_page_number: 7, bbox_norm: [0.3, 0.3, 0.6, 0.35] }] },
    { parameter_code: 'PZ-003', location: 'Объект', violation_label: 'VIOLATION_PRESENT', protocol_status: 'WARNING',
      criticality: 'Существенное (предписание)', pd_value: '100 м²', rd_value: '90 м²', id_value: null,
      evidence: [{ stage: 'PD', file_id: 'T001', pdf_page_number: 5 }] },
    { parameter_code: 'KR-058', location: 'Фундаментная плита', violation_label: 'NO_VIOLATION', protocol_status: 'OK',
      criticality: 'Критическое (приостановка работ)', pd_value: '1200 мм', rd_value: '1200 мм', id_value: null,
      evidence: [{ stage: 'PD', file_id: 'T001', pdf_page_number: 4 }] },
    { parameter_code: 'PZ-013', location: 'Объект', violation_label: 'MISSING_DOCUMENT', protocol_status: 'RD_MISSING',
      criticality: 'Критическое (приостановка работ)', pd_value: null, rd_value: null, id_value: null, evidence: [], note: 'в РД нет комплекта ТХ' },
    { parameter_code: 'PZ-001', location: 'Объект', violation_label: 'COMPARISON_IMPOSSIBLE', protocol_status: 'COMPARISON_IMPOSSIBLE',
      criticality: 'Критическое (приостановка работ)', pd_value: null, rd_value: null, id_value: null, evidence: [] },
    { parameter_code: 'PZ-022', location: 'Объект', violation_label: 'COMPARISON_IMPOSSIBLE', protocol_status: 'COMPARISON_IMPOSSIBLE',
      criticality: 'Критическое (приостановка работ)', pd_value: 'I/II', rd_value: null, id_value: null,
      evidence: [{ stage: 'PD', file_id: 'T001', pdf_page_number: 2 }], note: 'в РД значение не найдено' },
  ],
};

before(async () => {
  ensureDefaultUsers();
  loadParams();
  run("INSERT INTO objects(id, name, source, created_at) VALUES ('OBJ-TEST', 'Тестовый объект', 'upload', ?)", now());
  for (const [id, stage] of [['T001', 'PD'], ['T002', 'RD']]) {
    run("INSERT INTO files(id, object_id, stage, section, relative_path, extension, sha256, source) VALUES (?, 'OBJ-TEST', ?, 'KR', ?, '.pdf', ?, 'upload')",
      id, stage, path.join(tmp, `${id}.pdf`), id.repeat(16));
  }
  fs.writeFileSync(path.join(tmp, 'sub.json'), JSON.stringify(SUBMISSION));
  recordImport('OBJ-TEST', path.join(tmp, 'sub.json'), null, 'test');
  server = createApp().listen(0);
  await new Promise((r) => server.once('listening', r));
  base = `http://127.0.0.1:${server.address().port}/api/v1`;
  for (const login of ['inspector', 'supervisor', 'ml']) {
    tokens[login] = (await call('POST', '/auth/login', { body: { login, password: login } })).data.token;
  }
});

after(() => {
  server?.close();
});

test('служебные методы и спецификация доступны без входа', async () => {
  assert.equal((await call('GET', '/health')).status, 200);
  const spec = await call('GET', '/openapi.json');
  assert.equal(spec.data.openapi, '3.0.3');
  assert.match((await call('GET', '/metrics')).data, /inspector_http_requests_total/);
});

test('вход: неверный пароль — 401, без токена — 401', async () => {
  assert.equal((await call('POST', '/auth/login', { body: { login: 'inspector', password: 'x' } })).status, 401);
  assert.equal((await call('GET', '/objects')).status, 401);
  const me = await call('GET', '/auth/me', { token: tokens.inspector });
  assert.equal(me.data.role, 'inspector');
  assert.ok(me.data.permissions.includes('decide'));
});

test('запросы проверяются по OpenAPI', async () => {
  const r = await call('GET', '/objects?лишний=1', { token: tokens.inspector });
  assert.equal(r.status, 400);
  assert.equal(r.data.code, 'VALIDATION_ERROR');
  const bad = await call('POST', '/objects', { token: tokens.inspector, body: { address: 'без названия' } });
  assert.equal(bad.status, 400);
});

test('объект: создание и дубль кода', async () => {
  const r = await call('POST', '/objects', { token: tokens.inspector, body: { object_id: 'OBJ-NEW-1', name: 'Новый' } });
  assert.equal(r.status, 201);
  assert.equal(r.data.indicator, 'gray');
  assert.equal((await call('POST', '/objects', { token: tokens.inspector, body: { object_id: 'OBJ-NEW-1', name: 'Ещё' } })).status, 409);
  assert.equal((await call('POST', '/objects', { token: tokens.ml, body: { name: 'нет прав' } })).status, 403);
});

let protocolId;
let cands;

test('протокол: сводка и виды строк', async () => {
  const o = await call('GET', '/objects/OBJ-TEST', { token: tokens.inspector });
  assert.equal(o.data.indicator, 'yellow');
  protocolId = o.data.protocol.protocol_id;
  const p = await call('GET', `/protocols/${protocolId}`, { token: tokens.inspector });
  assert.equal(p.data.status, 'READY');
  assert.equal(p.data.summary.candidates.total, 2);
  assert.equal(p.data.summary.candidates.critical, 1);
  assert.equal(p.data.summary.rows.stub, 1);
  assert.equal(p.data.summary.rows.manual, 1);
  cands = (await call('GET', `/protocols/${protocolId}/checks?kind=candidate`, { token: tokens.inspector })).data;
  assert.equal(cands.length, 2);
  assert.equal(cands[0].parameter_code, 'KR-055');       // критические первыми
});

test('решения: отклонение требует причину и комментарий; ML-инженер решать не может', async () => {
  const url = `/protocols/${protocolId}/checks/${cands[0].check_id}/decision`;
  assert.equal((await call('PUT', url, { token: tokens.inspector, body: { status: 'NEGATIVE_VERIFIED' } })).data.code, 'REASON_REQUIRED');
  assert.equal((await call('PUT', url, { token: tokens.inspector, body: { status: 'NEGATIVE_VERIFIED', reason_code: 'OCR_ERROR' } })).data.code, 'COMMENT_REQUIRED');
  assert.equal((await call('PUT', url, { token: tokens.ml, body: { status: 'CONFIRMED_VIOLATION' } })).status, 403);
  const ok = await call('PUT', url, { token: tokens.inspector, body: { status: 'NEGATIVE_VERIFIED', reason_code: 'OCR_ERROR', comment: 'класс прочитан неверно' } });
  assert.equal(ok.status, 200);
  assert.equal(ok.data.protocol_status, 'VERIFYING');
  // по «не проверялось» решение не принимается
  const stub = (await call('GET', `/protocols/${protocolId}/checks?kind=stub`, { token: tokens.inspector })).data[0];
  assert.equal((await call('PUT', `/protocols/${protocolId}/checks/${stub.check_id}/decision`, { token: tokens.inspector, body: { status: 'CONFIRMED_VIOLATION' } })).status, 409);
});

test('финализация: только когда у всех кандидатов есть решение; после — изменения запрещены', async () => {
  const fin = await call('POST', `/protocols/${protocolId}/finalize`, { token: tokens.inspector, body: {} });
  assert.equal(fin.status, 409);
  assert.equal(fin.data.code, 'CANDIDATES_PENDING');
  const c = await call('PUT', `/protocols/${protocolId}/checks/${cands[1].check_id}/decision`, { token: tokens.inspector, body: { status: 'CONFIRMED_VIOLATION' } });
  assert.equal(c.data.protocol_status, 'VERIFICATION_COMPLETED');
  assert.ok(c.data.check.decision.recommendation.length > 20);     // шаблон рекомендации
  assert.equal((await call('POST', `/protocols/${protocolId}/finalize`, { token: tokens.inspector, body: {} })).status, 200);
  assert.equal((await call('PUT', `/protocols/${protocolId}/checks/${cands[1].check_id}/decision`, { token: tokens.inspector, body: { status: 'CLARIFICATION_REQUIRED' } })).status, 409);
  assert.equal((await call('POST', '/objects/OBJ-TEST/inspections', { token: tokens.inspector })).data.code, 'PROTOCOL_FINALIZED');
  assert.equal((await call('GET', '/objects/OBJ-TEST', { token: tokens.inspector })).data.indicator, 'red');
});

test('отмена финализации: только супервизор и только с причиной', async () => {
  assert.equal((await call('POST', `/protocols/${protocolId}/unfinalize`, { token: tokens.inspector, body: { reason: 'x' } })).status, 403);
  assert.equal((await call('POST', `/protocols/${protocolId}/unfinalize`, { token: tokens.supervisor, body: {} })).status, 400);
  const r = await call('POST', `/protocols/${protocolId}/unfinalize`, { token: tokens.supervisor, body: { reason: 'дозагрузка ИД' } });
  assert.equal(r.data.status, 'VERIFICATION_COMPLETED');
});

test('журнал аудита фиксирует решения и финализацию; инспектору недоступен', async () => {
  assert.equal((await call('GET', '/audit', { token: tokens.inspector })).status, 403);
  const rows = (await call('GET', '/audit?object_id=OBJ-TEST', { token: tokens.supervisor })).data;
  const actions = rows.map((r) => r.action);
  for (const a of ['DECISION', 'PROTOCOL_FINALIZE', 'PROTOCOL_UNFINALIZE']) assert.ok(actions.includes(a), a);
  const d = rows.find((r) => r.action === 'DECISION' && r.details.status === 'NEGATIVE_VERIFIED');
  assert.equal(d.details.reason_code, 'OCR_ERROR');
  assert.ok(d.ip);
});

test('выгрузка протокола: XML и JSON по разделам Приложения 2', async () => {
  const xml = await call('GET', `/protocols/${protocolId}/export?format=xml`, { token: tokens.inspector });
  assert.equal(xml.status, 200);
  assert.match(xml.data, /<section no="4" title="Критические нарушения">/);
  const json = (await call('GET', `/protocols/${protocolId}/export?format=json`, { token: tokens.inspector })).data;
  assert.equal(json.critical.length, 1);
  assert.equal(json.significant.length, 1);
  assert.equal(json.missing[0].missing, 'РД: нет комплекта ТХ');
  assert.equal(json.resolution.significant.length, 1);          // подтверждено одно существенное
  assert.equal(json.suspicions.length, 1);
});

test('новая версия протокола переносит решения по неизменившимся строкам', async () => {
  const changed = structuredClone(SUBMISSION);
  changed.checks[0].rd_value = 'B25';                            // KR-055: изменилось значение РД — решение не переносится
  changed.checks[1].id_value = '90 м²';                          // PZ-003: дозагружена ИД — решение переносится с пометкой
  fs.writeFileSync(path.join(tmp, 'sub2.json'), JSON.stringify(changed));
  const r = recordImport('OBJ-TEST', path.join(tmp, 'sub2.json'), null, 'test');
  assert.equal(r.version, 2);
  assert.equal(r.carried, 1);
  assert.equal(r.reset, 1);
  const rows = (await call('GET', `/protocols/${r.protocolId}/checks?kind=candidate`, { token: tokens.inspector })).data;
  const pz = rows.find((x) => x.parameter_code === 'PZ-003').decision;
  assert.equal(pz.carried_from_version, 1);
  assert.match(pz.carried_note, /данные ИД: — → 90 м²/);
  assert.equal(rows.find((x) => x.parameter_code === 'KR-055').decision, null);
  // решения в старой версии больше не принимаются
  assert.equal((await call('PUT', `/protocols/${protocolId}/checks/${cands[0].check_id}/decision`, { token: tokens.inspector, body: { status: 'CONFIRMED_VIOLATION' } })).data.code, 'NOT_LATEST_VERSION');
});
