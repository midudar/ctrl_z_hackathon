// Объект: статус загрузки стадий, обработка, протокол (сводка, действия, выгрузка), строки протокола по видам
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, download } from '../api.js';
import { useAuth, useRef_ } from '../App.jsx';
import {
  Badge, Crit, DecisionBadge, Dot, ErrorNote, INDICATOR, KIND, Modal, PROCESS_STATUS, PROTOCOL_STATUS, STAGE,
  fmtDate, plural, useToast, val,
} from '../ui.jsx';

const TABS = [
  ['candidate', 'Кандидаты'], ['manual', 'Ручная проверка'], ['negative', 'Нарушений нет'], ['missing', 'Нет документа'],
  ['stub', 'Не проверялось'], ['integrity', 'Комплект документов'], ['files', 'Файлы'], ['history', 'История'],
];

export default function ObjectPage() {
  const { objectId } = useParams();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [obj, setObj] = useState(null);
  const [versions, setVersions] = useState([]);
  const [protocol, setProtocol] = useState(null);
  const [checks, setChecks] = useState(null);
  const [error, setError] = useState(null);
  const [uploading, setUploading] = useState(params.get('upload') === '1');
  const [editing, setEditing] = useState(false);
  const [toastNode, toast] = useToast();
  const tab = params.get('tab') || 'candidate';
  const version = params.get('v');

  const loadObject = useCallback(async () => {
    const [o, vs] = await Promise.all([api(`/objects/${encodeURIComponent(objectId)}`), api(`/objects/${encodeURIComponent(objectId)}/protocols`)]);
    setObj(o);
    setVersions(vs);
    return { o, vs };
  }, [objectId]);

  const selected = useMemo(() => {
    if (!versions.length) return null;
    return versions.find((v) => String(v.version) === version) || versions[0];
  }, [versions, version]);

  const loadProtocol = useCallback(async (pid) => {
    const [p, c] = await Promise.all([api(`/protocols/${pid}`), api(`/protocols/${pid}/checks`)]);
    setProtocol(p);
    setChecks(c);
  }, []);

  useEffect(() => { loadObject().catch(setError); }, [loadObject]);
  useEffect(() => {
    if (selected) loadProtocol(selected.protocol_id).catch(setError);
    else { setProtocol(null); setChecks(null); }
  }, [selected, loadProtocol]);

  // Пока идёт обработка — опрашиваем статус процесса; по готовности перечитываем протокол
  const running = obj?.process && ['PENDING', 'PARSING'].includes(obj.process.status);
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(async () => {
      try {
        const { o } = await loadObject();
        if (!['PENDING', 'PARSING'].includes(o.process?.status)) {
          toast(o.process?.status === 'FAILED' ? 'Обработка завершилась ошибкой' : 'Проверка завершена: протокол готов', o.process?.status === 'FAILED' ? 'error' : 'ok');
          setParams((p) => { p.delete('v'); return p; }, { replace: true });
        }
      } catch { /* повторим */ }
    }, 3000);
    return () => clearInterval(t);
  }, [running, loadObject, toast, setParams]);

  const setTab = (t) => setParams((p) => { p.set('tab', t); return p; }, { replace: true });
  const setVersion = (v) => setParams((p) => { p.set('v', v); return p; }, { replace: true });

  async function startInspection() {
    try {
      await api(`/objects/${encodeURIComponent(objectId)}/inspections`, { method: 'POST' });
      toast('Проверка поставлена в очередь');
      await loadObject();
    } catch (e) { toast(e.message, 'error'); }
  }

  if (error) return <div className="page"><ErrorNote error={error} /><Link to="/">← К объектам</Link></div>;
  if (!obj) return <div className="page muted">Загрузка…</div>;

  const counts = {};
  for (const c of checks || []) counts[c.kind] = (counts[c.kind] || 0) + 1;
  const isLatest = protocol?.is_latest;

  return (
    <div className="page">
      {toastNode}
      <div className="crumbs"><Link to="/">Объекты</Link> / <span>{obj.name}</span></div>
      <div className="page-head">
        <div>
          <h1><Dot color={obj.indicator} title={INDICATOR[obj.indicator]} /> {obj.name}</h1>
          <p className="muted">
            {obj.object_id}
            {obj.address && <> · {obj.address}</>}
            {obj.case_number && <> · дело № {obj.case_number}</>}
            {obj.developer && <> · застройщик {obj.developer}</>}
            {can('manage_objects') && <> · <button type="button" className="link" onClick={() => setEditing(true)}>изменить сведения</button></>}
          </p>
        </div>
        <div className="head-actions">
          {can('upload') && <button type="button" className="btn" onClick={() => setUploading(true)} disabled={protocol?.status === 'PROTOCOL_FINALIZED'}>Загрузить документы</button>}
          {can('run') && <button type="button" className="btn btn-primary" onClick={startInspection} disabled={running || protocol?.status === 'PROTOCOL_FINALIZED'}>{running ? 'Идёт проверка…' : 'Запустить проверку'}</button>}
        </div>
      </div>

      <Stages stages={obj.stages} scenario={protocol?.scenario_text} />
      {obj.process && <ProcessPanel proc={obj.process} />}

      {!selected && !running && <div className="empty">Протокола пока нет. Загрузите документы ПД, РД и ИД — проверка запустится автоматически.</div>}

      {protocol && (
        <>
          <ProtocolPanel protocol={protocol} versions={versions} onVersion={setVersion} reload={async () => { await loadObject(); await loadProtocol(protocol.protocol_id); }} toast={toast} />
          <div className="tabs" role="tablist">
            {TABS.filter(([k]) => k !== 'history' || can('audit')).map(([k, label]) => (
              <button type="button" key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>
                {label}
                {KIND[k] && <span className="count">{counts[k] || 0}</span>}
                {k === 'integrity' && <span className="count">{protocol.integrity.length}</span>}
                {k === 'candidate' && protocol.summary.candidates.decisions.PENDING > 0 && <span className="count count-alert" title="ждут решения">{protocol.summary.candidates.decisions.PENDING}</span>}
              </button>
            ))}
          </div>
          {KIND[tab] && <ChecksTable rows={(checks || []).filter((c) => c.kind === tab)} kind={tab} protocolId={protocol.protocol_id} isLatest={isLatest} />}
          {tab === 'integrity' && <Integrity findings={protocol.integrity} />}
          {tab === 'files' && <Files objectId={objectId} />}
          {tab === 'history' && <History objectId={objectId} />}
        </>
      )}
      {uploading && <UploadDialog objectId={objectId} onClose={() => setUploading(false)} onDone={async (r) => { setUploading(false); toast(`Принято файлов: ${r.accepted.length}${r.rejected.length ? `, отклонено: ${r.rejected.length}` : ''}${r.process_id ? '; проверка запущена' : ''}`); await loadObject(); }} />}
      {editing && <EditObject obj={obj} onClose={() => setEditing(false)} onSaved={async () => { setEditing(false); await loadObject(); }} />}
    </div>
  );
}

function Stages({ stages, scenario }) {
  return (
    <section className="stages" aria-label="Статус загрузки документов">
      {stages.map((s) => (
        <div key={s.stage} className={`stage-card st-${s.status.split('_')[1].toLowerCase()}`}>
          <div className="stage-top"><b>{STAGE[s.stage]}</b> <span className="muted small">{s.stage_name}</span></div>
          <div className="stage-status">{s.status_text} <span className="muted small">· {s.files} {plural(s.files, 'файл', 'файла', 'файлов')}</span></div>
          <div className="muted small">{s.comment}</div>
        </div>
      ))}
      {scenario && <div className="stage-card st-scenario"><div className="stage-top"><b>Тип проверки</b></div><div className="stage-status">{scenario}</div></div>}
    </section>
  );
}

function ProcessPanel({ proc }) {
  const { can } = useAuth();
  const [log, setLog] = useState(null);
  const running = ['PENDING', 'PARSING'].includes(proc.status);
  if (!running && proc.status !== 'FAILED' && !log) {
    return (
      <div className="process-line muted small">
        Последняя обработка: {PROCESS_STATUS[proc.status] || proc.status} · {proc.message} · {fmtDate(proc.finished_at || proc.created_at)}
        {' '}· <button type="button" className="link" onClick={() => api(`/processes/${proc.process_id}`).then(setLog)}>журнал обработки</button>
      </div>
    );
  }
  return (
    <section className={`panel process ${proc.status === 'FAILED' ? 'process-failed' : ''}`}>
      <div className="process-head">
        <b>{PROCESS_STATUS[proc.status] || proc.status}</b>
        <span className="muted small">process_id {proc.process_id}</span>
      </div>
      {running && <div className="progress progress-lg"><div style={{ width: `${Math.max(3, Math.round((proc.progress || 0) * 100))}%` }} /></div>}
      <div className="small">{proc.message}{proc.error && <span className="t-red"> — {proc.error}</span>}</div>
      {running && <p className="muted small">Проверка идёт в фоне (обычно 1–4 минуты на объект), страницу можно закрыть.</p>}
      {log && (
        <>
          <pre className="log">{log.log || 'журнал пуст'}</pre>
          {log.log_truncated && !can('ml_data') && <p className="muted small">Показаны последние строки. Полный журнал — у ML-инженера.</p>}
          <button type="button" className="link small" onClick={() => setLog(null)}>скрыть журнал</button>
        </>
      )}
    </section>
  );
}

function Tile({ n, label, cls, sub }) {
  return <div className={`tile ${cls || ''}`}><div className="tile-n">{n}</div><div className="tile-l">{label}</div>{sub && <div className="tile-sub">{sub}</div>}</div>;
}

function ProtocolPanel({ protocol: p, versions, onVersion, reload, toast }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(null);
  const [unfinalizing, setUnfinalizing] = useState(false);
  const s = p.summary;
  const d = s.candidates.decisions;
  const done = s.candidates.total - d.PENDING;

  async function exp(format) {
    setBusy(format);
    try {
      const r = await download(`/protocols/${p.protocol_id}/export?format=${format}`, `protocol.${format}`);
      toast(`Протокол выгружен: ${r.name}${r.ms ? ` (${(r.ms / 1000).toFixed(1)} с)` : ''}`);
    } catch (e) { toast(e.message, 'error'); } finally { setBusy(null); }
  }
  async function finalize() {
    if (!window.confirm('Финализировать протокол? После этого решения и дозагрузка будут недоступны.')) return;
    try { await api(`/protocols/${p.protocol_id}/finalize`, { method: 'POST', body: {} }); toast('Протокол финализирован'); await reload(); } catch (e) { toast(e.message, 'error'); }
  }
  async function sendRin() {
    try {
      const r = await api(`/inspection/${p.process_id}`, { method: 'POST' });
      toast(r.message);
    } catch (e) { toast(e.message, 'error'); }
  }
  const firstPending = () => api(`/protocols/${p.protocol_id}/checks?kind=candidate&decision=PENDING`).then((rows) => {
    if (rows.length) navigate(`/protocols/${p.protocol_id}/checks/${rows[0].check_id}`);
  });

  return (
    <section className="panel protocol">
      <div className="protocol-head">
        <div>
          <h2>Протокол № {p.created_at.slice(0, 10)}-{p.object_id.replace(/^OBJ-/, '')}-v{p.version}</h2>
          <div className="small">
            <Badge cls={PROTOCOL_STATUS[p.status]?.cls}>{PROTOCOL_STATUS[p.status]?.label}</Badge>{' '}
            <span className="muted">
              сформирован {fmtDate(p.created_at)}
              {p.finalized_at && <> · финализирован {fmtDate(p.finalized_at)} ({p.finalized_by})</>}
              {p.sync_status && <> · ИАИС «РиН»: {p.sync_status}</>}
            </span>
          </div>
        </div>
        {versions.length > 1 && (
          <label className="small version-select">Версия
            <select value={p.version} onChange={(e) => onVersion(e.target.value)}>
              {versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {fmtDate(v.created_at)}{v.status === 'PROTOCOL_FINALIZED' ? ' · финал' : ''}</option>)}
            </select>
          </label>
        )}
      </div>
      {!p.is_latest && <div className="note">Это не последняя версия протокола — решения принимаются в последней.</div>}

      <div className="tiles">
        <Tile n={s.candidates.total} label="кандидатов в нарушения" cls="t-cand" sub={`критических ${s.candidates.critical} · существенных ${s.candidates.significant}`} />
        <Tile n={`${done} / ${s.candidates.total}`} label="обработано инспектором" sub={`подтверждено ${d.CONFIRMED_VIOLATION} · отклонено ${d.NEGATIVE_VERIFIED} · уточнение ${d.CLARIFICATION_REQUIRED}`} />
        <Tile n={s.parameters.checked} label="параметров сверено" sub={`из ${s.parameters_total}; нарушений нет — ${s.parameters.no_violation}`} cls="t-ok" />
        <Tile n={s.parameters.missing} label="не хватает документов" sub="отдельный перечень, не нарушения" cls="t-miss" />
        <Tile n={s.rows.manual} label="нужна ручная проверка" sub="подозрения ИИ" cls="t-manual" />
        <Tile n={s.parameters.not_checked} label="не проверялось системой" sub="извлечение не реализовано" cls="t-stub" />
      </div>

      <div className="protocol-actions">
        {p.actions.decide && d.PENDING > 0 && <button type="button" className="btn btn-primary" onClick={firstPending}>Начать верификацию ({d.PENDING})</button>}
        <span className="btn-group">
          <span className="muted small">Протокол:</span>
          {['pdf', 'docx', 'xml', 'json'].map((f) => (
            <button type="button" key={f} className="btn btn-sm" onClick={() => exp(f)} disabled={Boolean(busy)}>{busy === f ? '…' : f.toUpperCase()}</button>
          ))}
        </span>
        {p.is_latest && p.status !== 'PROTOCOL_FINALIZED' && (
          <button type="button" className="btn btn-success" onClick={finalize} disabled={!p.actions.finalize}
            title={p.actions.finalize ? '' : d.PENDING ? `Без решения ${d.PENDING} кандидат(ов)` : 'Недостаточно прав'}>
            Финализировать
          </button>
        )}
        {p.actions.unfinalize && <button type="button" className="btn" onClick={() => setUnfinalizing(true)}>Отменить финализацию</button>}
        {p.status === 'PROTOCOL_FINALIZED' && p.actions && <button type="button" className="btn" onClick={sendRin}>Передать в ИАИС «РиН»</button>}
      </div>
      {p.status !== 'PROTOCOL_FINALIZED' && d.PENDING > 0 && <p className="muted small">Финализация станет доступна, когда по всем кандидатам будет решение (подтвердить, отклонить или «требует уточнения»).</p>}
      <details className="versions-info small muted">
        <summary>Версии и трассируемость</summary>
        <div>{p.matrix_version}</div>
        <div>Модель: {p.model_version}</div>
        <div>Набор данных: {p.dataset_version}</div>
        <div>SHA-256 реестра входных файлов: <code>{p.input_manifest_hash}</code></div>
        <div>Процесс: <code>{p.process_id}</code></div>
      </details>
      {unfinalizing && <Unfinalize p={p} onClose={() => setUnfinalizing(false)} onDone={async () => { setUnfinalizing(false); toast('Финализация отменена'); await reload(); }} />}
    </section>
  );
}

function Unfinalize({ p, onClose, onDone }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState(null);
  async function submit(e) {
    e.preventDefault();
    try { await api(`/protocols/${p.protocol_id}/unfinalize`, { method: 'POST', body: { reason } }); onDone(); } catch (err) { setError(err); }
  }
  return (
    <Modal title="Отмена финализации" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <p className="small">Протокол вернётся в статус «Верификация завершена», дозагрузка снова станет возможной. Причина попадёт в журнал аудита.</p>
        <label>Причина *<textarea value={reason} onChange={(e) => setReason(e.target.value)} required rows={3} /></label>
        <ErrorNote error={error} />
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Отмена</button><button className="btn btn-danger">Отменить финализацию</button></div>
      </form>
    </Modal>
  );
}

function ChecksTable({ rows, kind, protocolId }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [crit, setCrit] = useState('all');
  const openable = kind === 'candidate' || kind === 'manual' || kind === 'negative';
  const shown = rows.filter((r) => (crit === 'all' || (crit === 'critical') === r.critical)
    && (!q || `${r.parameter_code} ${r.parameter_name} ${r.location} ${r.pd_value || ''} ${r.rd_value || ''} ${r.note || ''}`.toLowerCase().includes(q.toLowerCase())));
  const hint = {
    candidate: 'Система нашла расхождение по триггеру матрицы и приложила доказательства. Нарушением строка станет только после решения инспектора.',
    manual: 'Сравнение выполнено, но уверенности нет: значение найдено на одной стадии, сработала страховка или документы расходятся. Проверьте вручную.',
    negative: 'Значения на стадиях сверены, расхождения по триггеру нет. Доказательства приложены — можно проверить выборочно.',
    missing: 'Параметр нельзя проверить: в комплекте нет нужного документа. Это не нарушение — отдельный перечень для дозагрузки.',
    stub: 'Для этих параметров в системе пока нет извлечения — они не проверялись. Это не «данные несопоставимы».',
  }[kind];

  return (
    <section className="panel">
      <p className="muted small">{hint}</p>
      <div className="toolbar">
        <input className="search" placeholder="Поиск по коду, параметру, месту, значениям" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          {[['all', 'Все'], ['critical', 'Критические'], ['significant', 'Существенные']].map(([k, l]) => (
            <button type="button" key={k} className={crit === k ? 'on' : ''} onClick={() => setCrit(k)}>{l}</button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? <div className="empty">Строк нет.</div> : (
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Код</th><th>Параметр</th><th>Место</th><th>ПД</th><th>РД</th><th>ИД</th><th>Критичность</th>
                {(kind === 'candidate' || kind === 'manual') && <th>Решение</th>}
                {(kind === 'missing' || kind === 'manual') && <th>Пояснение</th>}
                {openable && <th>Доказ.</th>}
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.check_id} className={openable ? 'clickable' : ''} onClick={openable ? () => navigate(`/protocols/${protocolId}/checks/${r.check_id}`) : undefined}>
                  <td className="nowrap"><code>{r.parameter_code}</code></td>
                  <td>{r.parameter_name}<div className="muted small">{r.section}</div></td>
                  <td>{r.location || '—'}</td>
                  <td className="val">{val(r.pd_value)}</td>
                  <td className="val">{val(r.rd_value)}</td>
                  <td className="val">{val(r.id_value)}</td>
                  <td><Crit critical={r.critical} /></td>
                  {(kind === 'candidate' || kind === 'manual') && (
                    <td>
                      {r.decision || kind === 'candidate' ? <DecisionBadge status={r.decision?.status} /> : <span className="muted small">—</span>}
                      {r.decision?.carried_note && <div className="small t-pending" title={r.decision.carried_note}>данные ИД изменились — проверьте</div>}
                    </td>
                  )}
                  {(kind === 'missing' || kind === 'manual') && <td className="small">{val(r.note)}</td>}
                  {openable && <td className="nowrap small">{r.evidence.map((e) => STAGE[e.stage]).join(' · ') || '—'}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Integrity({ findings }) {
  const SEV = { CRITICAL: 'd-confirmed', HIGH: 'd-clarify', MEDIUM: 'd-pending', INFO: 'k-stub' };
  if (!findings.length) return <div className="empty">Дефектов комплекта не найдено.</div>;
  return (
    <section className="panel">
      <p className="muted small">Проверки самих файлов комплекта: пустые и битые файлы, временные файлы, дубли, сканы без текста, разделы ПД без комплектов РД, устаревшие редакции.</p>
      <ul className="findings">
        {findings.map((f, i) => (
          <li key={i}><Badge cls={SEV[f.severity] || ''}>{f.severity}</Badge> {f.title} <span className="muted small">({f.type})</span></li>
        ))}
      </ul>
    </section>
  );
}

function Files({ objectId }) {
  const [files, setFiles] = useState(null);
  const [stage, setStage] = useState('all');
  useEffect(() => { api(`/objects/${encodeURIComponent(objectId)}/files`).then(setFiles).catch(() => setFiles([])); }, [objectId]);
  if (!files) return <p className="muted">Загрузка…</p>;
  const shown = files.filter((f) => stage === 'all' || f.stage === stage);
  return (
    <section className="panel">
      <div className="seg">
        {['all', 'PD', 'RD', 'ID'].map((s) => <button type="button" key={s} className={stage === s ? 'on' : ''} onClick={() => setStage(s)}>{s === 'all' ? 'Все' : STAGE[s]} <span className="count">{s === 'all' ? files.length : files.filter((f) => f.stage === s).length}</span></button>)}
      </div>
      <div className="table-wrap">
        <table className="grid">
          <thead><tr><th>file_id</th><th>Стадия</th><th>Раздел</th><th>Файл</th><th>Размер</th><th>SHA-256</th><th>Источник</th></tr></thead>
          <tbody>
            {shown.map((f) => (
              <tr key={f.file_id}>
                <td><code>{f.file_id}</code></td><td>{STAGE[f.stage] || f.stage}</td><td>{f.section}</td>
                <td className="small">{f.name}{f.path && <div className="muted">{f.path.split('/').slice(0, -1).join(' / ')}</div>}</td>
                <td className="nowrap small">{f.size_bytes ? `${(f.size_bytes / 1048576).toFixed(1)} МБ` : '—'}</td>
                <td><code className="small">{f.sha256?.slice(0, 12)}…</code></td>
                <td className="small">{f.source === 'upload' ? 'загружен' : 'пакет'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function History({ objectId }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api(`/audit?object_id=${encodeURIComponent(objectId)}&limit=200`).then(setRows).catch(() => setRows([])); }, [objectId]);
  if (!rows) return <p className="muted">Загрузка…</p>;
  return <section className="panel"><AuditTable rows={rows} /></section>;
}

export function AuditTable({ rows, showObject }) {
  if (!rows.length) return <div className="empty">Записей нет.</div>;
  return (
    <div className="table-wrap">
      <table className="grid">
        <thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th>{showObject && <th>Объект</th>}<th>Подробности</th><th>IP</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="nowrap small">{fmtDate(r.ts)}</td>
              <td className="small">{r.user_login || 'система'}</td>
              <td className="small">{r.action_name}</td>
              {showObject && <td className="small"><code>{r.object_id || '—'}</code></td>}
              <td className="small details-cell">{describe(r)}</td>
              <td className="small muted nowrap">{r.ip || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function describe(r) {
  const d = r.details || {};
  if (r.action === 'DECISION') {
    return `${d.parameter_code} «${d.location || '—'}», v${d.protocol_version}: ${d.previous} → ${d.status}${d.reason_code ? ` (${d.reason_code})` : ''}${d.comment ? ` — ${d.comment}` : ''}`;
  }
  if (r.action === 'PROTOCOL_UNFINALIZE') return `v${d.protocol_version}, причина: ${d.reason}`;
  if (r.action === 'FILES_UPLOAD') return `${KIND_STAGE[d.stage] || d.stage}: ${(d.files || []).join(', ')}`;
  if (r.action === 'FILES_REJECTED') return (d.rejected || []).map((x) => `${x.name}: ${x.reason}`).join('; ');
  if (r.action === 'INSPECTION_DONE') return `протокол v${d.protocol_version}${d.carried ? `, перенесено решений ${d.carried}` : ''}${d.duration_s ? `, ${d.duration_s} с` : ''}`;
  return Object.entries(d).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ');
}
const KIND_STAGE = { PD: 'ПД', RD: 'РД', ID: 'ИД' };

function UploadDialog({ objectId, onClose, onDone }) {
  const ref = useRef_();
  const [stage, setStage] = useState('PD');
  const [section, setSection] = useState('AUTO');
  const [files, setFiles] = useState([]);
  const [start, setStart] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [drag, setDrag] = useState(false);
  const limits = ref?.limits || { max_file_mb: 50, max_batch_mb: 200 };
  const total = files.reduce((s, f) => s + f.size, 0);
  const tooBig = files.filter((f) => f.size > limits.max_file_mb * 1048576);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append('object_id', objectId);
    form.append('stage', stage);
    form.append('section', section);
    form.append('start', String(start));
    for (const f of files) form.append('files', f, f.name);
    try {
      const r = await api('/documents/upload', { method: 'POST', form });
      if (r.rejected.length) { setResult(r); setBusy(false); setFiles([]); if (r.accepted.length) onDone(r); return; }
      onDone(r);
    } catch (err) {
      if (err.status === 422) setResult({ accepted: [], rejected: err.body?.rejected || [] });
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal title="Загрузка документов" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <div className="form-row">
          <label>Стадия *
            <select value={stage} onChange={(e) => setStage(e.target.value)}>
              <option value="PD">ПД — проектная документация</option>
              <option value="RD">РД — рабочая документация</option>
              <option value="ID">ИД — исполнительная документация</option>
            </select>
          </label>
          <label>Раздел
            <select value={section} onChange={(e) => setSection(e.target.value)}>
              <option value="AUTO">Определить по шифру в имени файла</option>
              {['KR', 'AR', 'OV', 'VK', 'EOM', 'SS', 'GP', 'PB', 'POS', 'OTHER'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>
        <label className={`dropzone ${drag ? 'drag' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); setFiles([...files, ...e.dataTransfer.files]); }}>
          <input type="file" multiple accept=".pdf,.docx,.xml" onChange={(e) => setFiles([...files, ...e.target.files])} />
          <span>Перетащите файлы сюда или <u>выберите</u></span>
          <span className="muted small">PDF, DOCX, XML · до {limits.max_file_mb} МБ на файл, до {limits.max_batch_mb} МБ за раз · дубли и повреждённые PDF отклоняются</span>
        </label>
        {files.length > 0 && (
          <ul className="file-list small">
            {files.map((f, i) => (
              <li key={i} className={f.size > limits.max_file_mb * 1048576 ? 't-red' : ''}>
                {f.name} <span className="muted">{(f.size / 1048576).toFixed(1)} МБ</span>
                <button type="button" className="link" onClick={() => setFiles(files.filter((_, j) => j !== i))}>убрать</button>
              </li>
            ))}
            <li className="muted">всего {(total / 1048576).toFixed(1)} МБ</li>
          </ul>
        )}
        <label className="check"><input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} /> Сразу запустить проверку после загрузки</label>
        <p className="muted small">Стадию система берёт из вашего выбора, а не из содержимого файла. Шифр в имени файла (например, «…-РД-КЖ2.pdf») помогает определить раздел и редакцию — не переименовывайте файлы.</p>
        {result?.rejected?.length > 0 && (
          <div className="note note-error">Отклонено:<ul>{result.rejected.map((r, i) => <li key={i}>{r.name}: {r.reason}</li>)}</ul></div>
        )}
        {error && error.status !== 422 && <ErrorNote error={error} />}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>Закрыть</button>
          <button className="btn btn-primary" disabled={busy || !files.length || tooBig.length > 0 || total > limits.max_batch_mb * 1048576}>
            {busy ? 'Загрузка…' : `Загрузить ${files.length || ''}`}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditObject({ obj, onClose, onSaved }) {
  const [f, setF] = useState({ name: obj.name || '', address: obj.address || '', case_number: obj.case_number || '', developer: obj.developer || '', contractor: obj.contractor || '' });
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  async function submit(e) {
    e.preventDefault();
    try {
      await api(`/objects/${encodeURIComponent(obj.object_id)}`, { method: 'PATCH', body: Object.fromEntries(Object.entries(f).map(([k, v]) => [k, k === 'name' ? v : v || null])) });
      onSaved();
    } catch (err) { setError(err); }
  }
  return (
    <Modal title="Сведения об объекте" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>Наименование *<input value={f.name} onChange={set('name')} required /></label>
        <label>Адрес<input value={f.address} onChange={set('address')} /></label>
        <div className="form-row">
          <label>Номер надзорного дела<input value={f.case_number} onChange={set('case_number')} /></label>
          <label>Застройщик<input value={f.developer} onChange={set('developer')} /></label>
        </div>
        <label>Подрядчик<input value={f.contractor} onChange={set('contractor')} /></label>
        <ErrorNote error={error} />
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Отмена</button><button className="btn btn-primary">Сохранить</button></div>
      </form>
    </Modal>
  );
}
