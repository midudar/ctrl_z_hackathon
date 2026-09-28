import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { api, getToken, setToken, setUnauthorizedHandler } from './api.js';
import Login from './pages/Login.jsx';
import Objects from './pages/Objects.jsx';
import ObjectPage from './pages/ObjectPage.jsx';
import Review from './pages/Review.jsx';
import Audit from './pages/Audit.jsx';

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

const RefContext = createContext(null);
export const useRef_ = () => useContext(RefContext);   // справочники (причины отклонения и т. п.)

export default function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [reference, setReference] = useState(null);
  const navigate = useNavigate();

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    navigate('/login');
  }, [navigate]);

  useEffect(() => {
    setUnauthorizedHandler(() => { setToken(null); setUser(null); });
    if (!getToken()) { setReady(true); return; }
    api('/auth/me').then(setUser).catch(() => setToken(null)).finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (user) api('/reference').then(setReference).catch(() => {});
  }, [user]);

  if (!ready) return <div className="center-screen muted">Загрузка…</div>;

  const can = (perm) => Boolean(user?.permissions?.includes(perm));
  return (
    <AuthContext.Provider value={{ user, setUser, logout, can }}>
      <RefContext.Provider value={reference}>
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/*" element={user ? <Shell /> : <RedirectToLogin />} />
        </Routes>
      </RefContext.Provider>
    </AuthContext.Provider>
  );
}

function RedirectToLogin() {
  const loc = useLocation();
  return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
}

function Shell() {
  const { user, logout, can } = useAuth();
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden>✓</span>
          <span>Инспектор ИИ</span>
        </Link>
        <nav className="topnav">
          <NavLink to="/" end>Объекты</NavLink>
          {can('audit') && <NavLink to="/audit">Журнал аудита</NavLink>}
          {/* Swagger UI — для администратора и ML-инженера; инспектору не нужен (адрес /api/docs открыт всем) */}
          {['admin', 'ml_engineer'].includes(user.role) && <a href="/api/docs" target="_blank" rel="noreferrer">API</a>}
        </nav>
        <div className="topbar-user">
          <span className="user-name">{user.full_name}</span>
          <span className="role-chip">{user.role_name}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={logout}>Выйти</button>
        </div>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Objects />} />
          <Route path="/objects/:objectId" element={<ObjectPage />} />
          <Route path="/protocols/:protocolId/checks/:checkId" element={<Review />} />
          <Route path="/audit" element={can('audit') ? <Audit /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
