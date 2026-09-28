// Карточка доказательств и решение инспектора (ТЗ 9.3): ожидаемое и фактическое значения, страницы ПД / РД / ИД
// с подсвеченным фрагментом, файлы и SHA-256; «Подтвердить» — 1 клик, «Отклонить» — 3 (кнопка, причина, сохранить),
// «Требует уточнения» — 1. Клавиши: 1 / 2 / 3, ← / →.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, fileUrl, pageUrl } from '../api.js';
import { useAuth, useRef_ } from '../App.jsx';
import { Badge, Crit, DecisionBadge, ErrorNote, KIND, KindBadge, Modal, STAGE, fmtDate, useToast, val } from '../ui.jsx';

// Область вокруг рамки (как ml/render.context_region): рамка в центре, запас по сторонам
function contextRegion(b, minW = 0.34, minH = 0.22, grow = 3.2) {
  const cx = (b[0] + b[2]) / 2;
  const cy = (b[1] + b[3]) / 2;
  const w = Math.min(1, Math.max(minW, (b[2] - b[0]) * grow));
  const h = Math.min(1, Math.max(minH, (b[3] - b[1]) * grow));
  const left = Math.min(Math.max(0, cx - w / 2), 1 - w);
  const top = Math.min(Math.max(0, cy - h / 2), 1 - h);
  return [left, top, left + w, top + h];
}

function BoxOverlay({ bbox, region }) {
  const r = region || [0, 0, 1, 1];
  const rw = r[2] - r[0];
  const rh = r[3] - r[1];
  const style = {
    left: `${((bbox[0] - r[0]) / rw) * 100}%`, top: `${((bbox[1] - r[1]) / rh) * 100}%`,
    width: `${((bbox[2] - bbox[0]) / rw) * 100}%`, height: `${((bbox[3] - bbox[1]) / rh) * 100}%`,
  };
  return <div className="bbox" style={style} />;
}

function PageImage({ ev, region, max, onClick }) {
  const [state, setState] = useState('loading');
  const src = pageUrl(ev.file_id, ev.page, { max, region });
  return (
    <div className={`page-img ${onClick ? 'zoomable' : ''}`} onClick={onClick} role={onClick ? 'button' : undefined} tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter') onClick(); } : undefined}>
      {state === 'loading' && <div className="img-loading muted small">Отрисовка страницы…</div>}
      {state === 'error' && <div className="img-loading t-red small">Страница недоступна (файл не подключён?)</div>}
      <div className="img-wrap">
        <img src={src} alt={`${STAGE[ev.stage]} ${ev.file_id} стр. ${ev.page}`} onLoad={() => setState('ok')} onError={() => setState('error')} />
        {state === 'ok' && ev.bbox_norm && <BoxOverlay bbox={ev.bbox_norm} region={region} />}
      </div>
    </div>
  );
}

function EvidenceView({ ev, files }) {
  const [full, setFull] = useState(false);
  const region = ev.bbox_norm ? contextRegion(ev.bbox_norm) : null;
  const file = files.find((f) => f.file_id === ev.file_id);
  return (
    <div className={`evidence ev-${ev.stage}`}>
      <div className="evidence-head">
        <Badge cls={`stage-${ev.stage}`}>{STAGE[ev.stage]}</Badge>
        <b>{ev.file_id}</b> <span className="muted">стр. {ev.page}</span>
        <span className="evidence-links small">
          <button type="button" className="link" onClick={() => setFull(true)}>вся страница</button>
          <a className="link" href={`${fileUrl(ev.file_id)}#page=${ev.page}`} target="_blank" rel="noreferrer">открыть PDF</a>
        </span>
      </div>
      <div className="muted small evidence-file" title={ev.file_name}>{ev.file_name}</div>
      <PageImage ev={ev} region={region} max={1400} onClick={() => setFull(true)} />
      {ev.fragment && <div className="fragment small">«{ev.fragment}»</div>}
      <div className="muted tiny">
        {ev.bbox_source === 'ocr' ? 'фрагмент найден по распознанному тексту (OCR)' : 'фрагмент из текстового слоя PDF'}
        {file?.sha256 && <> · SHA-256 {file.sha256.slice(0, 12)}…</>}
      </div>
      {full && (
        <Modal title={`${STAGE[ev.stage]} · ${ev.file_id} · стр. ${ev.page}`} onClose={() => setFull(false)} wide>
          <p className="muted small">{ev.file_name}. Фрагмент обведён красным.</p>
          <div className="full-page"><PageImage ev={ev} region={null} max={3000} /></div>
        </Modal>
      )}
    </div>
  );
}

export default function Review() {
  const { protocolId, checkId } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const reference = useRef_();
  const [card, setCard] = useState(null);
  const [protocol, setProtocol] = useState(null);
  const [list, setList] = useState([]);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState(null);              // null | 'reject'
  const [reason, setReason] = useState(null);
  const [comment, setComment] = useState('');
  const [recommendation, setRecommendation] = useState('');
  const [busy, setBusy] = useState(false);
  const [onlyPending, setOnlyPending] = useState(true);
  const [toastNode, toast] = useToast();
  const commentRef = useRef(null);

  const load = useCallback(async () => {
    setError(null);
    const [c, p] = await Promise.all([api(`/protocols/${protocolId}/checks/${checkId}`), api(`/protocols/${protocolId}`)]);
    setCard(c);
    setProtocol(p);
    setMode(null);
    setReason(null);
    setComment('');
    setRecommendation(c.decision?.recommendation || c.recommendation_template);
    const rows = await api(`/protocols/${protocolId}/checks?kind=${c.kind}`);
    setList(rows);
  }, [protocolId, checkId]);

  useEffect(() => { load().catch(setError); }, [load]);

  const index = list.findIndex((r) => r.check_id === Number(checkId));
  const pendingIds = useMemo(() => list.filter((r) => !r.decision).map((r) => r.check_id), [list]);
  const go = useCallback((id) => navigate(`/protocols/${protocolId}/checks/${id}`), [navigate, protocolId]);
  const prev = index > 0 ? list[index - 1].check_id : null;
  const next = index >= 0 && index < list.length - 1 ? list[index + 1].check_id : null;
  const decidable = protocol?.actions?.decide && card && ['candidate', 'manual', 'negative'].includes(card.kind);

  // следующий кандидат без решения после текущего (по кругу)
  const nextPending = useCallback((exclude) => {
    const order = [...list.slice(index + 1), ...list.slice(0, Math.max(index, 0))];
    return order.find((r) => !r.decision && r.check_id !== exclude)?.check_id ?? null;
  }, [list, index]);

  async function decide(status) {
    if (!card) return;
    if (status === 'NEGATIVE_VERIFIED' && (!reason || !comment.trim())) { commentRef.current?.focus(); return; }
    setBusy(true);
    try {
      const body = { status };
      if (status === 'NEGATIVE_VERIFIED') body.reason_code = reason;
      if (comment.trim()) body.comment = comment.trim();
      if (status === 'CONFIRMED_VIOLATION' && recommendation.trim()) body.recommendation = recommendation.trim();
      await api(`/protocols/${protocolId}/checks/${card.check_id}/decision`, { method: 'PUT', body });
      const label = { CONFIRMED_VIOLATION: 'Нарушение подтверждено', NEGATIVE_VERIFIED: 'Кандидат отклонён', CLARIFICATION_REQUIRED: 'Отправлено на уточнение' }[status];
      const target = onlyPending ? nextPending(card.check_id) : next;
      if (target) { toast(`${label}. Следующий кандидат`); go(target); } else { toast(`${label}. Все кандидаты обработаны`); await load(); }
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function resetDecision() {
    try { await api(`/protocols/${protocolId}/checks/${card.check_id}/decision`, { method: 'DELETE' }); toast('Решение отменено'); await load(); } catch (e) { toast(e.message, 'error'); }
  }

  function pickReason(code) {
    setReason(code);
    if (!comment.trim()) setComment(reference?.reason_codes?.[code] || code);
  }

  // Выход из отклонения: комментарий, подставленный из причины, не должен уйти вместе с другим решением
  function cancelReject() {
    if (reason && comment === (reference?.reason_codes?.[reason] || reason)) setComment('');
    setMode(null);
    setReason(null);
  }

  // Клавиши
  useEffect(() => {
    const h = (e) => {
      if (e.target.closest('input, textarea, select') || e.ctrlKey || e.metaKey || e.altKey || document.querySelector('.modal')) return;
      if (e.key === 'ArrowLeft' && prev) go(prev);
      else if (e.key === 'ArrowRight' && next) go(next);
      else if (decidable && e.key === '1') decide('CONFIRMED_VIOLATION');
      else if (decidable && e.key === '2') setMode('reject');
      else if (decidable && e.key === '3') decide('CLARIFICATION_REQUIRED');
      else if (e.key === 'Escape') cancelReject();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });

  if (error) return <div className="page"><ErrorNote error={error} /><Link to="/">← К объектам</Link></div>;
  if (!card || !protocol) return <div className="page muted">Загрузка…</div>;

  const p = card.parameter;
  const d = card.decision;
  const byStage = ['PD', 'RD', 'ID'].map((s) => [s, card[`${s.toLowerCase()}_value`]]);
  const reasons = reference?.reason_codes || {};

  return (
    <div className="page review">
      {toastNode}
      <div className="crumbs">
        <Link to="/">Объекты</Link> / <Link to={`/objects/${encodeURIComponent(protocol.object_id)}?tab=${card.kind}`}>{protocol.object_id}</Link> / протокол v{protocol.version} / {KIND[card.kind].label.toLowerCase()}
      </div>

      <div className="review-nav">
        <button type="button" className="btn btn-sm" disabled={!prev} onClick={() => go(prev)} title="Клавиша ←">← Предыдущий</button>
        <span className="small">
          {KIND[card.kind].short} <b>{index + 1}</b> из {list.length}
          {card.kind === 'candidate' && <span className="muted"> · без решения {pendingIds.length}</span>}
        </span>
        <button type="button" className="btn btn-sm" disabled={!next} onClick={() => go(next)} title="Клавиша →">Следующий →</button>
        {card.kind === 'candidate' && (
          <label className="check small"><input type="checkbox" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} /> после решения — к следующему без решения</label>
        )}
      </div>

      <div className="review-grid">
        <div className="review-main">
          <section className="panel card-head">
            <div className="card-badges"><KindBadge kind={card.kind} /> <Crit critical={card.critical} /> <Badge cls="muted-badge">приоритет {card.review_priority}</Badge></div>
            <h1><code>{card.parameter_code}</code> {card.parameter_name}</h1>
            <div className="card-location">
              <span className="muted">Место:</span> <b>{card.location || 'объект'}</b>
              <span className="muted"> · {p.section || card.section}</span>
              {!p.in_matrix && <span className="muted"> · вне матрицы (свободный поиск)</span>}
            </div>
            {p.trigger && <div className="trigger"><span className="muted small">Триггер матрицы — почему это нарушение:</span><div>{p.trigger}</div></div>}
          </section>

          <section className="values">
            {byStage.map(([s, v]) => (
              <div key={s} className={`value-card stage-${s} ${v == null ? 'empty' : ''}`}>
                <div className="muted small">{STAGE[s]} — {{ PD: 'что утверждено', RD: 'по чему строят', ID: 'что построено' }[s]}</div>
                <div className="value">{val(v)}</div>
                <div className="muted tiny">{{ PD: p.source_pd, RD: p.source_rd, ID: p.source_id }[s]}</div>
              </div>
            ))}
          </section>
          {(card.note || card.deviation) && <div className="note"><b>Отклонение:</b> {card.note || card.deviation}</div>}

          <section className="evidence-grid" aria-label="Доказательства">
            {card.evidence.length === 0 && <div className="empty">Доказательств нет.</div>}
            {card.evidence.map((ev, i) => <EvidenceView key={`${ev.file_id}-${ev.page}-${i}`} ev={ev} files={card.files} />)}
          </section>
        </div>

        <aside className="review-side">
          <section className="panel decision-panel">
            <h2>Решение инспектора</h2>
            {d ? (
              <div className="current-decision">
                <DecisionBadge status={d.status} />
                {d.reason_code && <div className="small">Причина: {reasons[d.reason_code] || d.reason_code}</div>}
                {d.comment && <div className="small">«{d.comment}»</div>}
                <div className="muted tiny">{d.decided_by || '—'}, {fmtDate(d.decided_at)}{d.carried_from_version && ` · перенесено из версии ${d.carried_from_version}${d.carried_note ? '' : ' (строка не изменилась после дозагрузки)'}`}</div>
                {d.carried_note && <div className="note small">Решение перенесено, но {d.carried_note}.</div>}
                {decidable && <button type="button" className="link small" onClick={resetDecision}>отменить решение</button>}
              </div>
            ) : card.kind === 'candidate' ? <DecisionBadge status="PENDING" /> : <p className="muted small">Решение по этой строке необязательно — финализация требует решений только по кандидатам.</p>}

            {!decidable && protocol.status === 'PROTOCOL_FINALIZED' && <p className="muted small">Протокол финализирован — решения изменить нельзя.</p>}
            {!decidable && !protocol.is_latest && <p className="muted small">Это не последняя версия протокола.</p>}
            {!decidable && protocol.is_latest && protocol.status !== 'PROTOCOL_FINALIZED' && !can('decide') && <p className="muted small">Решения принимает инспектор.</p>}

            {decidable && (
              <>
                {mode !== 'reject' ? (
                  <div className="decision-buttons">
                    <button type="button" className="btn btn-danger btn-lg" disabled={busy} onClick={() => decide('CONFIRMED_VIOLATION')}>
                      Подтвердить нарушение <kbd>1</kbd>
                    </button>
                    <button type="button" className="btn btn-lg" disabled={busy} onClick={() => setMode('reject')}>
                      Отклонить <kbd>2</kbd>
                    </button>
                    <button type="button" className="btn btn-warn btn-lg" disabled={busy} onClick={() => decide('CLARIFICATION_REQUIRED')}>
                      Требует уточнения <kbd>3</kbd>
                    </button>
                  </div>
                ) : (
                  <div className="reject-box">
                    <div className="small"><b>Причина отклонения</b> (обязательно):</div>
                    <div className="reasons">
                      {Object.entries(reasons).map(([code, label]) => (
                        <button type="button" key={code} className={`chip ${reason === code ? 'on' : ''}`} onClick={() => pickReason(code)}>{label}</button>
                      ))}
                    </div>
                    <label className="small comment-label">Комментарий (обязательно; подставляется из причины — дополните при необходимости)
                      <textarea ref={commentRef} rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Обоснование отклонения" />
                    </label>
                    <div className="form-actions">
                      <button type="button" className="btn" onClick={cancelReject}>Назад</button>
                      <button type="button" className="btn btn-primary" disabled={busy || !reason || !comment.trim()} onClick={() => decide('NEGATIVE_VERIFIED')}>Сохранить отклонение</button>
                    </div>
                  </div>
                )}
                {mode !== 'reject' && (
                  <label className="small comment-label">Комментарий (необязательно)
                    <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Обоснование решения" />
                  </label>
                )}
                {card.kind !== 'negative' && (
                  <details className="small">
                    <summary>Рекомендация в резолютивную часть протокола</summary>
                    <textarea rows={5} value={recommendation} onChange={(e) => setRecommendation(e.target.value)} />
                    <div className="muted tiny">Попадёт в раздел 7 протокола при подтверждении нарушения.</div>
                  </details>
                )}
              </>
            )}
          </section>

          <section className="panel small">
            <h3>Документы</h3>
            <ul className="doc-list">
              {card.files.map((f) => (
                <li key={f.file_id}><Badge cls={`stage-${f.stage === 'RD_ID_MIXED' ? 'RD' : f.stage}`}>{STAGE[f.stage] || f.stage}</Badge> <code>{f.file_id}</code> {f.name}<div className="muted tiny">SHA-256 {f.sha256}</div></li>
              ))}
            </ul>
            <p className="muted tiny">Редакции: система сравнивает только действующие (последние) редакции документов; устаревшие исключены — см. «Комплект документов».</p>
            <p className="muted tiny">Группа доказательств: <code>{card.evidence_group_id}</code> · статус системы: {card.finding_status}</p>
          </section>
          <p className="muted tiny keys">Клавиши: <kbd>1</kbd> подтвердить · <kbd>2</kbd> отклонить · <kbd>3</kbd> уточнение · <kbd>←</kbd> <kbd>→</kbd> навигация</p>
        </aside>
      </div>
    </div>
  );
}
