// Асинхронная обработка (ТЗ 1.4, 1.5): задача «проверить объект» → Python-воркер → события о ходе и результате.
//   AMQP_URL задан  — задача публикуется в RabbitMQ (очередь inspector.jobs), воркер отвечает в inspector.events;
//   AMQP_URL пуст   — backend сам запускает воркер отдельным процессом: `python -m ml.worker job <задача.json>`,
//                     события читаются из его stdout построчно (тот же формат JSON).
// Код обработки у воркера один и тот же, различается только транспорт.
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { one, run, now, all } from './db.js';
import { log } from './log.js';
import { writeObjectMeta, filesSnapshot } from './files.js';
import { importSubmission, readJson } from './importer.js';
import { audit, ACTIONS } from './audit.js';
import { metrics } from './metrics.js';

let channel = null;          // AMQP-канал
const localQueue = [];       // локальный режим: задачи по одной, чтобы не перегрузить машину
let localBusy = false;

export function queueMode() {
  return config.amqpUrl ? 'rabbitmq' : 'local';
}

export function queueDepth() {
  return config.amqpUrl ? metrics.gauges.queue_depth ?? 0 : localQueue.length + (localBusy ? 1 : 0);
}

export async function startQueue() {
  if (!config.amqpUrl) {
    log.info('очередь: локальный режим (воркер запускается процессом python)', { ml_root: config.mlRoot });
  } else {
    await connectAmqp();
  }
  // Задачи, не доведённые до конца до перезапуска, ставятся в очередь снова
  for (const p of all("SELECT * FROM processes WHERE status IN ('PENDING', 'PARSING') AND kind = 'inspection'")) {
    log.warn('процесс не завершён до перезапуска — повторная постановка в очередь', { process_id: p.id });
    dispatch(p.id);
  }
  setInterval(checkTimeouts, 60 * 1000).unref();
}

async function connectAmqp() {
  const amqp = await import('amqplib');
  for (let attempt = 1; ; attempt++) {
    try {
      const conn = await amqp.connect(config.amqpUrl);
      conn.on('error', (e) => log.error('RabbitMQ: ошибка соединения', { error: String(e) }));
      conn.on('close', () => {
        log.warn('RabbitMQ: соединение закрыто, переподключение через 5 с');
        channel = null;
        setTimeout(() => connectAmqp().catch((e) => log.error('RabbitMQ: не удалось переподключиться', { error: String(e) })), 5000);
      });
      channel = await conn.createChannel();
      await channel.assertQueue(config.jobsQueue, { durable: true });
      await channel.assertQueue(config.eventsQueue, { durable: true });
      await channel.consume(config.eventsQueue, (msg) => {
        if (!msg) return;
        try {
          handleEvent(JSON.parse(msg.content.toString('utf8')));
        } catch (e) {
          log.error('RabbitMQ: не удалось обработать событие', { error: String(e) });
        }
        channel.ack(msg);
      });
      setInterval(async () => {
        try {
          if (channel) metrics.gauges.queue_depth = (await channel.checkQueue(config.jobsQueue)).messageCount;
        } catch { /* очередь недоступна — метрика не обновляется */ }
      }, 15000).unref();
      log.info('очередь: RabbitMQ подключён', { jobs: config.jobsQueue, events: config.eventsQueue });
      return;
    } catch (e) {
      log.warn(`RabbitMQ недоступен (попытка ${attempt}), повтор через 5 с`, { error: String(e) });
      await new Promise((r) => setTimeout(r, 5000));
    }
  }
}

// Создать процесс проверки объекта и поставить в очередь
export function createInspection(objectId, user) {
  const id = crypto.randomUUID();
  const outDir = path.join(config.dataDir, 'processes', id);
  run(`INSERT INTO processes(id, object_id, kind, status, message, created_at, out_dir, requested_by)
       VALUES (?, ?, 'inspection', 'PENDING', 'в очереди', ?, ?, ?)`, id, objectId, now(), outDir, user?.id ?? null);
  metrics.counters.inspections_started += 1;
  dispatch(id);
  return id;
}

function jobMessage(proc) {
  fs.mkdirSync(proc.out_dir, { recursive: true });
  const docs = config.packageDocs || path.join(config.dataDir, 'uploads');
  return {
    process_id: proc.id,
    object_id: proc.object_id,
    docs_dir: docs,
    meta_dir: writeObjectMeta(proc.object_id),
    out_dir: proc.out_dir,
    cache_dir: config.mlCache || null,
    pages_dir: path.join(config.dataDir, 'pages'),
    render_max: 2000,
    attempt: proc.attempts + 1,
  };
}

function dispatch(processId) {
  const proc = one('SELECT * FROM processes WHERE id = ?', processId);
  const job = jobMessage(proc);
  run("UPDATE processes SET status = 'PENDING', attempts = attempts + 1, message = 'в очереди' WHERE id = ?", processId);
  if (config.amqpUrl) {
    if (!channel) {
      log.warn('RabbitMQ ещё не подключён — задача будет отправлена при подключении', { process_id: processId });
      setTimeout(() => dispatch(processId), 5000);
      run('UPDATE processes SET attempts = attempts - 1 WHERE id = ?', processId);
      return;
    }
    channel.sendToQueue(config.jobsQueue, Buffer.from(JSON.stringify(job)), { persistent: true, contentType: 'application/json' });
    log.info('задача отправлена в RabbitMQ', { process_id: processId, object_id: proc.object_id });
  } else {
    localQueue.push(job);
    pumpLocal();
  }
}

const children = new Map();

function pumpLocal() {
  if (localBusy || !localQueue.length) return;
  localBusy = true;
  const job = localQueue.shift();
  const jobFile = path.join(job.out_dir, 'job.json');
  fs.writeFileSync(jobFile, JSON.stringify(job, null, 2), 'utf8');
  const child = spawn(config.python, ['-m', 'ml.worker', 'job', jobFile], {
    cwd: config.mlRoot,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });
  children.set(job.process_id, child);
  let buf = '';
  child.stdout.on('data', (d) => {
    buf += d.toString('utf8');
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      try {
        handleEvent(JSON.parse(line));
      } catch {
        appendLog(job.process_id, line);
      }
    }
  });
  child.stderr.on('data', (d) => appendLog(job.process_id, d.toString('utf8').trimEnd(), true));
  child.on('error', (e) => {
    handleEvent({ type: 'failed', process_id: job.process_id, error: `не удалось запустить Python (${config.python}): ${e.message}` });
  });
  child.on('close', (code) => {
    children.delete(job.process_id);
    const p = one('SELECT status FROM processes WHERE id = ?', job.process_id);
    if (p && (p.status === 'PENDING' || p.status === 'PARSING')) {
      handleEvent({ type: 'failed', process_id: job.process_id, error: `воркер завершился с кодом ${code} без результата` });
    }
    localBusy = false;
    pumpLocal();
  });
}

function appendLog(processId, text, isErr = false) {
  // MuPDF пишет предупреждения о шрифтах внутри PDF — на результат они не влияют
  const lines = text.split(/\r?\n/).filter((l) => l && !l.startsWith('MuPDF error: syntax error: missing font'));
  if (!lines.length) return;
  run("UPDATE processes SET log = substr(log || ?, -200000) WHERE id = ?", lines.map((l) => (isErr ? '! ' : '') + l).join('\n') + '\n', processId);
}

function handleEvent(evt) {
  const proc = one('SELECT * FROM processes WHERE id = ?', evt.process_id);
  if (!proc) return;
  if (evt.type === 'started') {
    run("UPDATE processes SET status = 'PARSING', started_at = ?, message = ? WHERE id = ?", now(), evt.message || 'обработка документов', proc.id);
  } else if (evt.type === 'progress') {
    run("UPDATE processes SET status = 'PARSING', message = ?, progress = ? WHERE id = ?", evt.message, evt.progress ?? proc.progress, proc.id);
    if (evt.line) appendLog(proc.id, evt.line);
  } else if (evt.type === 'log') {
    appendLog(proc.id, evt.line || '');
  } else if (evt.type === 'done') {
    finishProcess(proc, evt);
  } else if (evt.type === 'failed') {
    failProcess(proc, evt.error || 'неизвестная ошибка');
  }
}

function finishProcess(proc, evt) {
  try {
    const submission = readJson(evt.submission);
    const integrity = evt.integrity && fs.existsSync(evt.integrity) ? readJson(evt.integrity) : submission.document_integrity || null;
    const r = importSubmission({
      objectId: proc.object_id, processId: proc.id, submission, integrity, submissionPath: evt.submission,
      modelVersion: evt.model_version || 'inspector-ml',
    });
    const warn = evt.exit_code === 1 ? '; ответ записан с ошибками проверки схемы (см. журнал)' : '';
    run(`UPDATE processes SET status = 'DONE', finished_at = ?, exit_code = ?, progress = 1, protocol_id = ?,
           message = ? WHERE id = ?`,
    now(), evt.exit_code ?? 0, r.protocolId,
    `протокол версии ${r.version} готов: строк ${r.rows}` + (r.carried ? `, перенесено решений ${r.carried}` : '')
      + (r.reset ? `, изменилось после дозагрузки ${r.reset}` : '') + warn, proc.id);
    metrics.counters.inspections_done += 1;
    audit(null, 'INSPECTION_DONE', proc.object_id, { process_id: proc.id, protocol_version: r.version, carried: r.carried, reset: r.reset, duration_s: evt.duration });
  } catch (e) {
    failProcess(proc, `не удалось загрузить результат: ${e.message}`);
  }
}

function failProcess(proc, error) {
  const cur = one('SELECT * FROM processes WHERE id = ?', proc.id);
  if (cur.status === 'DONE' || cur.status === 'FAILED') return;
  appendLog(proc.id, `ОШИБКА: ${error}`, true);
  if (cur.attempts <= config.jobRetries) {
    log.warn('обработка не удалась, повтор', { process_id: proc.id, attempt: cur.attempts, error });
    run("UPDATE processes SET message = ? WHERE id = ?", `ошибка, повтор (попытка ${cur.attempts + 1})`, proc.id);
    dispatch(proc.id);
    return;
  }
  run("UPDATE processes SET status = 'FAILED', finished_at = ?, error = ?, message = 'обработка завершилась ошибкой' WHERE id = ?", now(), error, proc.id);
  metrics.counters.inspections_failed += 1;
  // ТЗ 9.1: при неудаче после повторов — уведомление администратора (здесь — ERROR в журнале и аудите)
  log.error('обработка не удалась после всех попыток — требуется внимание администратора', { process_id: proc.id, object_id: proc.object_id, error });
  audit(null, 'INSPECTION_FAILED', proc.object_id, { process_id: proc.id, error });
}

function checkTimeouts() {
  const limit = config.jobTimeoutMin * 60 * 1000;
  for (const p of all("SELECT * FROM processes WHERE status = 'PARSING' AND kind = 'inspection'")) {
    if (p.started_at && Date.now() - Date.parse(p.started_at) > limit) {
      children.get(p.id)?.kill();
      failProcess(p, `превышено время обработки (${config.jobTimeoutMin} мин)`);
    }
  }
}

// Процесс «импорт готового ответа» — для объектов пакета при первом запуске стенда
export function recordImport(objectId, submissionPath, integrityPath, modelVersion) {
  const id = crypto.randomUUID();
  run(`INSERT INTO processes(id, object_id, kind, status, message, created_at, started_at, finished_at, exit_code, progress, attempts)
       VALUES (?, ?, 'import', 'DONE', ?, ?, ?, ?, 0, 1, 1)`,
  id, objectId, 'импортирован готовый ответ ML', now(), now(), now());
  const submission = readJson(submissionPath);
  const integrity = integrityPath && fs.existsSync(integrityPath) ? readJson(integrityPath) : submission.document_integrity || null;
  const r = importSubmission({ objectId, processId: id, submission, integrity, submissionPath, modelVersion });
  run('UPDATE processes SET protocol_id = ?, message = ? WHERE id = ?', r.protocolId, `протокол версии ${r.version}: строк ${r.rows} (готовый ответ ML)`, id);
  return r;
}

export { filesSnapshot };
