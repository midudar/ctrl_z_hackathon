// Дашборд: объекты с цветовой индикацией, фильтры, создание объекта
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api.js';
import { useAuth } from '../App.jsx';
import { Badge, Dot, ErrorNote, INDICATOR, Modal, PROCESS_STATUS, PROTOCOL_STATUS, fmtDate } from '../ui.jsx';

export default function Objects() {
  const { can } = useAuth();
  const [objects, setObjects] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api('/objects').then((o) => alive && setObjects(o)).catch((e) => alive && setError(e));
    load();
    const t = setInterval(load, 8000);             // статус обработки меняется в фоне
    return () => { alive = false; clearInterval(t); };
  }, []);

  const counts = useMemo(() => {
    const c = { all: 0, red: 0, yellow: 0, green: 0, gray: 0 };
    for (const o of objects || []) { c.all += 1; c[o.indicator] += 1; }
    return c;
  }, [objects]);

  const shown = (objects || []).filter((o) => (filter === 'all' || o.indicator === filter)
    && (!q || `${o.name} ${o.object_id} ${o.address || ''}`.toLowerCase().includes(q.toLowerCase())));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Объекты</h1>
          <p className="muted">Проверки документации объектов капитального строительства</p>
        </div>
        {can('manage_objects') && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>+ Новый объект</button>}
      </div>

      <div className="toolbar">
        <div className="seg" role="tablist" aria-label="Фильтр по индикатору">
          {[['all', 'Все'], ['red', 'Нарушения'], ['yellow', 'Ждут решения'], ['green', 'Без нарушений'], ['gray', 'Без протокола']].map(([k, l]) => (
            <button type="button" key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)} role="tab" aria-selected={filter === k}>
              {k !== 'all' && <Dot color={k} />} {l} <span className="count">{counts[k]}</span>
            </button>
          ))}
        </div>
        <input className="search" placeholder="Поиск по названию, коду, адресу" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <ErrorNote error={error} />
      {!objects && !error && <p className="muted">Загрузка…</p>}
      {objects && shown.length === 0 && <div className="empty">Объектов нет{filter !== 'all' || q ? ' по этому фильтру' : ''}.</div>}

      <div className="object-list">
        {shown.map((o) => <ObjectRow key={o.object_id} o={o} />)}
      </div>

      <div className="legend muted small">
        {Object.entries(INDICATOR).map(([k, v]) => <span key={k}><Dot color={k} /> {v}</span>)}
      </div>
      {creating && <CreateObject onClose={() => setCreating(false)} />}
    </div>
  );
}

function ObjectRow({ o }) {
  const p = o.protocol;
  const proc = o.process;
  const running = proc && (proc.status === 'PENDING' || proc.status === 'PARSING');
  const d = p?.candidates?.decisions;
  return (
    <Link to={`/objects/${encodeURIComponent(o.object_id)}`} className={`object-row ind-${o.indicator}`}>
      <div className="object-main">
        <div className="object-title"><Dot color={o.indicator} /> <b>{o.name}</b></div>
        <div className="muted small">
          {o.object_id}
          {o.dataset_role && <Badge cls="role">{o.dataset_role === 'TEST' ? 'тестовый объект' : 'обучающий объект'}</Badge>}
          {o.address && <> · {o.address}</>}
        </div>
      </div>
      <div className="object-files small">
        <span title="Проектная документация">ПД <b>{o.files.PD}</b></span>
        <span title="Рабочая документация">РД <b>{o.files.RD}</b></span>
        <span title="Исполнительная документация">ИД <b>{o.files.ID}</b></span>
      </div>
      <div className="object-protocol small">
        {p ? (
          <>
            <div><Badge cls={PROTOCOL_STATUS[p.status]?.cls}>{PROTOCOL_STATUS[p.status]?.label}</Badge> <span className="muted">v{p.version}</span></div>
            <div className="muted">
              кандидатов {p.candidates.total}
              {d.PENDING > 0 && <> · <b className="t-pending">ждут решения {d.PENDING}</b></>}
              {p.confirmed.total > 0 && <> · <b className="t-red">подтверждено {p.confirmed.total}</b></>}
            </div>
          </>
        ) : <span className="muted">протокола нет</span>}
      </div>
      <div className="object-process small">
        {proc ? (
          running ? (
            <>
              <div>{PROCESS_STATUS[proc.status]}</div>
              <div className="progress"><div style={{ width: `${Math.round((proc.progress || 0) * 100)}%` }} /></div>
            </>
          ) : proc.status === 'FAILED'
            ? <span className="t-red">Ошибка обработки</span>
            : <span className="muted">обновлено {fmtDate(proc.finished_at || proc.created_at)}</span>
        ) : <span className="muted">не проверялся</span>}
      </div>
    </Link>
  );
}

function CreateObject({ onClose }) {
  const navigate = useNavigate();
  const [f, setF] = useState({ name: '', address: '', case_number: '', developer: '', contractor: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim()));
      const o = await api('/objects', { method: 'POST', body });
      navigate(`/objects/${encodeURIComponent(o.object_id)}?upload=1`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal title="Новый объект" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>Наименование объекта *<input value={f.name} onChange={set('name')} required autoFocus /></label>
        <label>Адрес<input value={f.address} onChange={set('address')} /></label>
        <div className="form-row">
          <label>Номер надзорного дела<input value={f.case_number} onChange={set('case_number')} /></label>
          <label>Застройщик<input value={f.developer} onChange={set('developer')} /></label>
        </div>
        <label>Подрядчик<input value={f.contractor} onChange={set('contractor')} /></label>
        <p className="muted small">Сведения попадут в шапку протокола. Документы ПД, РД и ИД загружаются на следующем шаге.</p>
        <ErrorNote error={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button className="btn btn-primary" disabled={busy}>Создать и загрузить документы</button>
        </div>
      </form>
    </Modal>
  );
}
