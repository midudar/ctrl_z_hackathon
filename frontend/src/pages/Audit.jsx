// Журнал аудита (супервизор, администратор, ML-инженер): кто, что, когда, с какого адреса
import { useEffect, useState } from 'react';
import { api, download } from '../api.js';
import { useAuth } from '../App.jsx';
import { ErrorNote, useToast } from '../ui.jsx';
import { AuditTable } from './ObjectPage.jsx';

const ACTIONS = {
  '': 'Все действия', LOGIN: 'Вход', LOGIN_FAILED: 'Неудачный вход', DECISION: 'Решения', DECISION_RESET: 'Отмена решений',
  FILES_UPLOAD: 'Загрузка файлов', FILES_REJECTED: 'Отклонённые файлы', INSPECTION_START: 'Запуск проверки', INSPECTION_DONE: 'Проверка завершена',
  INSPECTION_FAILED: 'Ошибки обработки', PROTOCOL_FINALIZE: 'Финализация', PROTOCOL_UNFINALIZE: 'Отмена финализации', PROTOCOL_EXPORT: 'Выгрузка протокола',
  RIN_SEND: 'Передача в ИАИС «РиН»', OBJECT_CREATE: 'Создание объекта',
};

export default function Audit() {
  const { can } = useAuth();
  const [rows, setRows] = useState(null);
  const [action, setAction] = useState('');
  const [error, setError] = useState(null);
  const [toastNode, toast] = useToast();

  useEffect(() => {
    setRows(null);
    api(`/audit?limit=500${action ? `&action=${action}` : ''}`).then(setRows).catch(setError);
  }, [action]);

  return (
    <div className="page">
      {toastNode}
      <div className="page-head">
        <div>
          <h1>Журнал аудита</h1>
          <p className="muted">Каждое действие пользователя: время, IP-адрес, тип действия, объект. Решения инспектора — с прежним и новым статусом.</p>
        </div>
        {can('ml_data') && (
          <button type="button" className="btn" onClick={() => download('/ml/feedback-dataset', 'feedback_dataset_draft.jsonl').then(() => toast('Выгружен черновик GOLD-набора')).catch((e) => toast(e.message, 'error'))}>
            Решения для дообучения (JSONL)
          </button>
        )}
      </div>
      <div className="toolbar">
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Тип действия">
          {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      <ErrorNote error={error} />
      {rows ? <section className="panel"><AuditTable rows={rows} showObject /></section> : !error && <p className="muted">Загрузка…</p>}
    </div>
  );
}
