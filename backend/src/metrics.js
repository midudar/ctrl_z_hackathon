// Метрики для мониторинга (ТЗ 13.4) в формате Prometheus: GET /api/v1/metrics
import os from 'node:os';

export const metrics = {
  counters: {
    http_requests_total: 0, http_errors_5xx_total: 0, inspections_started: 0, inspections_done: 0, inspections_failed: 0,
    decisions_total: 0, logins_total: 0, logins_failed_total: 0,
  },
  gauges: { queue_depth: 0 },
  durations: [],            // последние 1000 длительностей запросов, мс
  sessions: new Map(),      // user_id → время последнего запроса
};

export function observeRequest(ms, status, userId) {
  metrics.counters.http_requests_total += 1;
  if (status >= 500) metrics.counters.http_errors_5xx_total += 1;
  metrics.durations.push(ms);
  if (metrics.durations.length > 1000) metrics.durations.shift();
  if (userId) metrics.sessions.set(userId, Date.now());
}

function quantile(q) {
  if (!metrics.durations.length) return 0;
  const s = [...metrics.durations].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))];
}

export function prometheus(extra = {}) {
  const lines = [];
  const put = (name, value, help, type = 'gauge') => {
    lines.push(`# HELP ${name} ${help}`, `# TYPE ${name} ${type}`, `${name} ${value}`);
  };
  const c = metrics.counters;
  put('inspector_http_requests_total', c.http_requests_total, 'HTTP-запросов всего', 'counter');
  put('inspector_http_errors_5xx_total', c.http_errors_5xx_total, 'Ответов с кодом 5xx', 'counter');
  put('inspector_http_request_ms_p50', quantile(0.5), 'Медиана времени ответа, мс');
  put('inspector_http_request_ms_p95', quantile(0.95), '95-й процентиль времени ответа, мс');
  put('inspector_inspections_started_total', c.inspections_started, 'Запущено проверок', 'counter');
  put('inspector_inspections_done_total', c.inspections_done, 'Завершено проверок', 'counter');
  put('inspector_inspections_failed_total', c.inspections_failed, 'Проверок с ошибкой', 'counter');
  put('inspector_decisions_total', c.decisions_total, 'Решений инспекторов', 'counter');
  put('inspector_logins_total', c.logins_total, 'Успешных входов', 'counter');
  put('inspector_logins_failed_total', c.logins_failed_total, 'Неудачных входов', 'counter');
  const active = [...metrics.sessions.values()].filter((t) => Date.now() - t < 15 * 60 * 1000).length;
  put('inspector_active_sessions', active, 'Пользователей с запросами за 15 минут');
  put('inspector_queue_depth', extra.queueDepth ?? metrics.gauges.queue_depth, 'Задач в очереди обработки');
  const mem = process.memoryUsage();
  put('inspector_process_rss_bytes', mem.rss, 'Память процесса backend, байт');
  put('inspector_process_cpu_seconds', (process.cpuUsage().user + process.cpuUsage().system) / 1e6, 'Время CPU backend, с', 'counter');
  put('inspector_host_load1', os.loadavg()[0], 'Средняя загрузка хоста за 1 минуту');
  if (extra.diskFreeBytes != null) put('inspector_data_disk_free_bytes', extra.diskFreeBytes, 'Свободно на диске данных, байт');
  return lines.join('\n') + '\n';
}
