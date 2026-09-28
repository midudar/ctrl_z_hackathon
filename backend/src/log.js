// Структурированные логи (ТЗ, раздел 13): JSON в stdout, поля timestamp, level, service, message, request_id, user_id.
// DEBUG пишется только при LOG_LEVEL=DEBUG (тестовый контур).
import { config } from './config.js';

const LEVELS = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 };
const threshold = LEVELS[(process.env.LOG_LEVEL || 'INFO').toUpperCase()] ?? LEVELS.INFO;

function write(level, message, ctx = {}) {
  if (LEVELS[level] < threshold) return;
  const { request_id = null, user_id = null, ...extra } = ctx;
  const line = {
    timestamp: new Date().toISOString(), level, service: config.serviceName, message, request_id, user_id, ...extra,
  };
  (level === 'ERROR' ? process.stderr : process.stdout).write(JSON.stringify(line) + '\n');
}

export const log = {
  debug: (m, c) => write('DEBUG', m, c),
  info: (m, c) => write('INFO', m, c),
  warn: (m, c) => write('WARNING', m, c),
  error: (m, c) => write('ERROR', m, c),
};
