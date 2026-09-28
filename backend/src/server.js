// Точка входа backend «Инспектор ИИ»
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { config } from './config.js';
import { log } from './log.js';
import { ensureDefaultUsers } from './auth.js';
import { loadParams } from './domain.js';
import { seedPackage } from './seed.js';
import { startQueue } from './queue.js';
import { createApp } from './app.js';

async function main() {
  ensureDefaultUsers();
  const n = loadParams();
  log.info('матрица параметров загружена', { params: n });
  if (config.seed) seedPackage();
  await startQueue();
  const app = createApp();
  const server = config.tlsCert && config.tlsKey
    ? https.createServer({ cert: fs.readFileSync(config.tlsCert), key: fs.readFileSync(config.tlsKey), minVersion: 'TLSv1.3' }, app)
    : http.createServer(app);
  server.listen(config.port, config.host, () => {
    log.info(`backend слушает ${config.tlsCert ? 'https' : 'http'}://${config.host}:${config.port}`, {
      data_dir: config.dataDir, ml_root: config.mlRoot, package_docs: config.packageDocs ?? null, queue: config.amqpUrl ? 'rabbitmq' : 'local',
    });
  });
}

main().catch((e) => {
  log.error(`backend не запустился: ${e.stack || e}`);
  process.exit(1);
});
