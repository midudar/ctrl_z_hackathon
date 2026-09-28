// Настройки backend. Всё задаётся переменными окружения; значения по умолчанию рассчитаны на локальный запуск
// без Docker из репозитория ML (service/backend лежит внутри него) или из командного репозитория (../ml-service).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BACKEND = path.resolve(HERE, '..');

function env(name, fallback) {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

// Папка ML-части: там, где лежит ml/run.py
function findMlRoot() {
  const candidates = [env('ML_ROOT'), path.resolve(BACKEND, '..', '..'), path.resolve(BACKEND, '..', 'ml-service'),
    path.resolve(BACKEND, '..', '..', 'ml-service')].filter(Boolean);
  return candidates.find((c) => fs.existsSync(path.join(c, 'ml', 'run.py'))) || candidates[0];
}

const ML_ROOT = findMlRoot();
const PACKAGE = path.join(ML_ROOT, '01_ПАКЕТ', 'ХАКАТОН_УЧАСТНИКАМ_ГОТОВО_К_ПЕРЕДАЧЕ');
const firstExisting = (...ps) => ps.find((p) => p && fs.existsSync(p));

export const config = {
  port: Number(env('PORT', 8080)),
  host: env('HOST', '0.0.0.0'),
  // HTTPS: если заданы сертификат и ключ, сервер поднимается по TLS (в проде TLS обычно завершает прокси)
  tlsCert: env('TLS_CERT'),
  tlsKey: env('TLS_KEY'),

  dataDir: path.resolve(env('DATA_DIR', path.join(BACKEND, '..', 'data'))),
  frontendDist: path.resolve(env('FRONTEND_DIST', path.join(BACKEND, '..', 'frontend', 'dist'))),

  // ML-часть
  mlRoot: ML_ROOT,
  python: env('PYTHON', process.platform === 'win32' ? 'python' : 'python3'),
  // Документы и метаданные пакета организаторов (для объектов из пакета)
  packageDocs: env('PACKAGE_DOCS', firstExisting(path.join(PACKAGE, '01_ДОКУМЕНТАЦИЯ'))),
  packageData: env('PACKAGE_DATA', firstExisting(path.join(PACKAGE, '02_ФОРМАТ_ДАННЫХ_И_ПРИМЕРЫ', 'data'))),
  // Готовые ответы ML (out/submission_*.json) — импортируются как первая версия протокола, чтобы стенд
  // показывал результат сразу, не дожидаясь прогона
  seedResults: env('SEED_RESULTS', firstExisting(path.join(ML_ROOT, 'out'))),
  seed: env('SEED', '1') !== '0',
  mlCache: env('ML_CACHE_DIR', firstExisting(path.join(ML_ROOT, 'out', 'cache'))),

  // Очередь: amqp://… — задачи уходят в RabbitMQ, их берёт Python-воркер (docker-compose);
  // пусто — backend сам запускает воркер отдельным процессом (локально, без брокера)
  amqpUrl: env('AMQP_URL'),
  jobsQueue: env('AMQP_JOBS_QUEUE', 'inspector.jobs'),
  eventsQueue: env('AMQP_EVENTS_QUEUE', 'inspector.events'),
  // Синхронные вызовы ML (картинка страницы, протокол): http://ml-worker:8000 — по REST к воркеру;
  // пусто — отдельным процессом python
  mlHttpUrl: env('ML_HTTP_URL'),
  jobTimeoutMin: Number(env('JOB_TIMEOUT_MIN', 30)),
  jobRetries: Number(env('JOB_RETRIES', 2)),       // ТЗ 9.1: таймаут обработки — повтор до 2 раз

  // Безопасность
  jwtSecret: env('JWT_SECRET'),
  tokenTtlHours: Number(env('TOKEN_TTL_HOURS', 12)),
  // Пароли пользователей по умолчанию — только для стенда; в эксплуатации задать свои
  defaultUsers: [
    { login: 'inspector', full_name: 'Иванова А. С.', role: 'inspector', password: env('PASS_INSPECTOR', 'inspector') },
    { login: 'supervisor', full_name: 'Петров Д. В.', role: 'supervisor', password: env('PASS_SUPERVISOR', 'supervisor') },
    { login: 'admin', full_name: 'Администратор', role: 'admin', password: env('PASS_ADMIN', 'admin') },
    { login: 'ml', full_name: 'ML-инженер', role: 'ml_engineer', password: env('PASS_ML', 'ml') },
  ],

  // Загрузка (ТЗ 9.1, «Обработка ошибок при загрузке»)
  maxFileMb: Number(env('MAX_FILE_MB', 50)),
  maxBatchMb: Number(env('MAX_BATCH_MB', 200)),
  allowedExt: ['.pdf', '.docx', '.xml'],

  serviceName: 'inspector-backend',
  version: '1.0.0',
};

for (const d of ['', 'uploads', 'objects', 'processes', 'pages', 'tmp', 'exports']) {
  fs.mkdirSync(path.join(config.dataDir, d), { recursive: true });
}
