// Журнал аудита (ТЗ 9.x, 12.4): кто, что, над каким объектом, когда, с какого адреса
import { run, now } from './db.js';
import { log } from './log.js';

export function audit(req, action, objectId = null, details = null) {
  const user = req?.user;
  const ip = req ? (req.headers['x-forwarded-for']?.split(',')[0].trim() || req.socket?.remoteAddress || null) : null;
  run(`INSERT INTO audit_log(ts, user_id, user_login, action, object_id, details, ip, user_agent, request_id)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  now(), user?.id ?? null, user?.login ?? null, action, objectId,
  details == null ? null : JSON.stringify(details), ip, req?.headers['user-agent'] ?? null, req?.id ?? null);
  log.info(`audit: ${action}`, { request_id: req?.id ?? null, user_id: user?.id ?? null, object_id: objectId });
}

// Названия действий для журнала в интерфейсе
export const ACTIONS = {
  LOGIN: 'Вход в систему',
  LOGIN_FAILED: 'Неудачная попытка входа',
  OBJECT_CREATE: 'Создан объект',
  OBJECT_UPDATE: 'Изменены сведения об объекте',
  FILES_UPLOAD: 'Загружены файлы',
  FILES_REJECTED: 'Файлы отклонены при загрузке',
  INSPECTION_START: 'Запущена проверка',
  INSPECTION_DONE: 'Проверка завершена',
  INSPECTION_FAILED: 'Проверка завершилась ошибкой',
  DECISION: 'Решение по кандидату',
  DECISION_RESET: 'Решение отменено',
  PROTOCOL_FINALIZE: 'Протокол финализирован',
  PROTOCOL_UNFINALIZE: 'Отменена финализация протокола',
  PROTOCOL_EXPORT: 'Выгружен протокол',
  FILE_DOWNLOAD: 'Скачан файл',
  RIN_SEND: 'Передача в ИАИС «РиН»',
  DATASET_EXPORT: 'Выгружены решения для дообучения',
};
