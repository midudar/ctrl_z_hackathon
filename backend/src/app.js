// Express-приложение: request_id и JSON-логи, метрики, заголовки безопасности, проверка запросов по OpenAPI 3.0,
// REST API /api/v1, Swagger UI (/api/docs) и собранный интерфейс (frontend/dist).
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import * as OpenApiValidator from 'express-openapi-validator';
import swaggerUi from 'swagger-ui-dist';
import yaml from 'js-yaml';
import { config } from './config.js';
import { log } from './log.js';
import { authenticate } from './auth.js';
import { HttpError } from './errors.js';
import { api } from './routes.js';
import { observeRequest, prometheus } from './metrics.js';
import { queueDepth, queueMode } from './queue.js';
import { one } from './db.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SPEC_PATH = path.join(HERE, '..', 'openapi.yaml');

export function createApp() {
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', 'loopback');

  app.use((req, res, next) => {
    req.id = req.headers['x-request-id'] || crypto.randomUUID();
    res.setHeader('X-Request-Id', req.id);
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'no-referrer');
    const t0 = process.hrtime.bigint();
    res.on('finish', () => {
      const ms = Number(process.hrtime.bigint() - t0) / 1e6;
      observeRequest(ms, res.statusCode, req.user?.id);
      if (req.path.startsWith('/api/')) {
        const level = res.statusCode >= 500 ? 'error' : 'info';
        log[level](`${req.method} ${req.path} ${res.statusCode}`, {
          request_id: req.id, user_id: req.user?.id ?? null, status: res.statusCode, duration_ms: Math.round(ms),
        });
      }
    });
    next();
  });

  app.use(express.json({ limit: '1mb' }));
  app.use(authenticate);

  // Служебные: здоровье и метрики Prometheus (ТЗ 13.4–13.5)
  app.get('/api/v1/health', (_req, res) => {
    let dbOk = true;
    try { one('SELECT 1 AS ok'); } catch { dbOk = false; }
    res.status(dbOk ? 200 : 503).json({
      status: dbOk ? 'ok' : 'degraded', service: config.serviceName, version: config.version, queue: queueMode(),
      ml: config.mlHttpUrl ? 'http' : 'process', time: new Date().toISOString(),
    });
  });
  app.get('/api/v1/metrics', (_req, res) => {
    let diskFreeBytes = null;
    try { const s = fs.statfsSync(config.dataDir); diskFreeBytes = s.bavail * s.bsize; } catch { /* нет statfs */ }
    res.type('text/plain; version=0.0.4').send(prometheus({ queueDepth: queueDepth(), diskFreeBytes }));
  });

  // Контракт API и Swagger UI
  app.get('/api/v1/openapi.yaml', (_req, res) => res.type('text/yaml').sendFile(SPEC_PATH));
  app.get('/api/v1/openapi.json', (_req, res) => res.json(yaml.load(fs.readFileSync(SPEC_PATH, 'utf8'))));
  app.use('/api/docs', express.static(swaggerUi.getAbsoluteFSPath(), { index: false }));
  app.get('/api/docs', (_req, res) => res.redirect('/api/docs/index.html?url=/api/v1/openapi.yaml'));
  app.get('/api/docs/swagger-initializer.js', (_req, res) => res.type('js').send(
    `window.onload = () => { window.ui = SwaggerUIBundle({ url: '/api/v1/openapi.yaml', dom_id: '#swagger-ui', persistAuthorization: true }); };`,
  ));

  // Проверка запросов по спецификации (ТЗ 1.3: «обязательная валидация схемы OpenAPI 3.0»).
  // Загрузка файлов (multipart) проверяется в обработчике: валидатор буферизовал бы файлы в памяти.
  app.use(OpenApiValidator.middleware({
    apiSpec: SPEC_PATH,
    validateRequests: { allowUnknownQueryParameters: false },
    validateResponses: false,
    validateSecurity: false,
    ignorePaths: (p) => !p.startsWith('/api/v1/') || p.startsWith('/api/v1/documents/upload'),
    fileUploader: false,
  }));

  app.use('/api/v1', api);
  app.use('/api', (_req, _res, next) => next(new HttpError(404, 'NOT_FOUND', 'Такого метода API нет')));

  // Интерфейс: собранный React (SPA — все прочие пути отдают index.html)
  if (fs.existsSync(path.join(config.frontendDist, 'index.html'))) {
    app.use(express.static(config.frontendDist, { index: false, maxAge: '1h' }));
    app.get(/.*/, (_req, res) => res.sendFile(path.join(config.frontendDist, 'index.html')));
  } else {
    app.get('/', (_req, res) => res.type('text').send('Инспектор ИИ: API работает (/api/docs). Интерфейс не собран: npm run build в service/frontend.'));
  }

  // Ошибки — единый формат { code, message, details, request_id }
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, _next) => {
    let status = err.status || 500;
    let code = err.code || 'INTERNAL_ERROR';
    let message = err.message;
    let details = err.details;
    if (!(err instanceof HttpError) && err.errors && status < 500) {        // ошибка валидации OpenAPI
      code = 'VALIDATION_ERROR';
      message = 'Запрос не соответствует спецификации API';
      details = err.errors.map((e) => ({ path: e.path, message: e.message }));
    }
    if (status >= 500 && !(err instanceof HttpError)) {
      log.error(`необработанная ошибка: ${err.stack || err}`, { request_id: req.id, user_id: req.user?.id ?? null });
      message = 'Внутренняя ошибка сервера';
      status = 500;
    }
    res.status(status).json({ code, message, details, request_id: req.id });
  });
  return app;
}
