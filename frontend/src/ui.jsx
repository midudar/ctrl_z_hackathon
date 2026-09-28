// Общие элементы интерфейса: метки статусов, модальное окно, уведомления
import { useEffect, useState } from 'react';

export const KIND = {
  candidate: { label: 'Кандидат в нарушения', short: 'Кандидат', cls: 'k-candidate' },
  manual: { label: 'Нужна ручная проверка', short: 'Ручная проверка', cls: 'k-manual' },
  negative: { label: 'Проверено, нарушений нет', short: 'Нарушений нет', cls: 'k-negative' },
  missing: { label: 'Не хватает документа', short: 'Нет документа', cls: 'k-missing' },
  stub: { label: 'Не проверялось системой', short: 'Не проверялось', cls: 'k-stub' },
};

export const DECISION = {
  PENDING: { label: 'Ожидает решения', cls: 'd-pending' },
  CONFIRMED_VIOLATION: { label: 'Нарушение подтверждено', cls: 'd-confirmed' },
  NEGATIVE_VERIFIED: { label: 'Отклонено', cls: 'd-rejected' },
  CLARIFICATION_REQUIRED: { label: 'Требует уточнения', cls: 'd-clarify' },
};

export const PROTOCOL_STATUS = {
  READY: { label: 'Ожидает верификации', cls: 's-ready' },
  VERIFYING: { label: 'Идёт верификация', cls: 's-verifying' },
  VERIFICATION_COMPLETED: { label: 'Верификация завершена', cls: 's-completed' },
  PROTOCOL_FINALIZED: { label: 'Финализирован', cls: 's-final' },
};

export const PROCESS_STATUS = {
  PENDING: 'В очереди', PARSING: 'Обработка документов', READY: 'Протокол готов', VERIFYING: 'Идёт верификация',
  COMPLETED: 'Верификация завершена', FINALIZED: 'Протокол финализирован', FAILED: 'Ошибка обработки',
};

export const INDICATOR = {
  red: 'Есть подтверждённые нарушения', yellow: 'Кандидаты ждут решения', green: 'Нарушений нет, всё обработано', gray: 'Протокола пока нет',
};

export const STAGE = { PD: 'ПД', RD: 'РД', ID: 'ИД', RD_ID_MIXED: 'РД+ИД' };

export function Badge({ cls, children, title }) {
  return <span className={`badge ${cls || ''}`} title={title}>{children}</span>;
}

export function KindBadge({ kind }) {
  const k = KIND[kind] || KIND.stub;
  return <Badge cls={k.cls}>{k.short}</Badge>;
}

export function DecisionBadge({ status }) {
  const d = DECISION[status || 'PENDING'];
  return <Badge cls={d.cls}>{d.label}</Badge>;
}

export function Crit({ critical }) {
  return critical
    ? <Badge cls="crit-high" title="Критическое (приостановка работ)">Критическое</Badge>
    : <Badge cls="crit-mid" title="Существенное (предписание)">Существенное</Badge>;
}

export function Dot({ color, title }) {
  return <span className={`dot dot-${color}`} title={title || INDICATOR[color]} />;
}

export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Закрыть">✕</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return (
    <div className="note note-error">
      {error.message}
      {Array.isArray(error.details) && error.details.length > 0 && error.details[0]?.message && (
        <ul>{error.details.slice(0, 5).map((d, i) => <li key={i}>{d.path}: {d.message}</li>)}</ul>
      )}
    </div>
  );
}

export function useToast() {
  const [toast, setToast] = useState(null);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), toast.kind === 'error' ? 7000 : 3500);
    return () => clearTimeout(t);
  }, [toast]);
  const node = toast ? <div className={`toast toast-${toast.kind || 'ok'}`} role="status">{toast.text}</div> : null;
  return [node, (text, kind) => setToast({ text, kind })];
}

export const val = (v) => (v == null || v === '' ? '—' : v);

export function fmtDate(iso, withTime = true) {
  if (!iso) return '—';
  const d = new Date(iso);
  return withTime
    ? d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('ru-RU');
}

export function plural(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
