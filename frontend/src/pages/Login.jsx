import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, setToken } from '../api.js';
import { useAuth } from '../App.jsx';

const DEMO = [
  ['inspector', 'Инспектор — верификация кандидатов'],
  ['supervisor', 'Супервизор — ещё отмена финализации'],
  ['admin', 'Администратор'],
  ['ml', 'ML-инженер — логи и данные для дообучения'],
];

export default function Login() {
  const { setUser } = useAuth();
  const [login, setLogin] = useState('inspector');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const loc = useLocation();

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api('/auth/login', { method: 'POST', body: { login, password } });
      setToken(r.token);
      setUser(r.user);
      navigate(loc.state?.from || '/', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-mark brand-mark-lg" aria-hidden>✓</span>
          <div>
            <h1>Инспектор ИИ</h1>
            <p className="muted">Сверка проектной, рабочей и исполнительной документации по 132 параметрам</p>
          </div>
        </div>
        <form onSubmit={submit} className="form">
          <label>Логин
            <input value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" required />
          </label>
          <label>Пароль
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required autoFocus />
          </label>
          {error && <div className="note note-error">{error}</div>}
          <button className="btn btn-primary btn-block" disabled={busy}>{busy ? 'Вход…' : 'Войти'}</button>
        </form>
        <div className="login-demo">
          <p className="muted small">Учётные записи стенда (пароль совпадает с логином):</p>
          <ul>
            {DEMO.map(([l, d]) => (
              <li key={l}>
                <button type="button" className="link" onClick={() => { setLogin(l); setPassword(l); }}>{l}</button>
                <span className="muted small"> — {d}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="muted small login-foot">Система не выносит вердикт: она готовит кандидатов в нарушения с доказательствами, решение принимает инспектор.</p>
      </div>
    </div>
  );
}
