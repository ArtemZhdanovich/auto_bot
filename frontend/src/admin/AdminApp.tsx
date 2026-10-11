/**
 * Админ-панель (UI/UX ТЗ §3): вход по internal token, источники,
 * партнёры (создание/ключ-один-раз/ротация), лиды. Роуты /admin/*.
 * Экраны — в AdminScreens.tsx.
 */

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Activity, Car, Users } from "lucide-react";

import {
  AdminApiError,
  aapi,
  clearAdminToken,
  getAdminToken,
  setAdminToken,
  type AdminPartner,
} from "./api";
import { AdminLeads, Partners, Sources } from "./AdminScreens";
import { ChatWidget } from "./ChatWidget";
import { ErrorState } from "../ui/States";

function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setAdminToken(token.trim());
    try {
      await aapi<unknown>("/adapters");
      onAuthed();
    } catch (e) {
      clearAdminToken();
      setError(
        e instanceof AdminApiError && e.status === 401
          ? "Неверный токен"
          : "Сервис недоступен",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-secondary)",
      }}
    >
      <form
        className="panel"
        style={{ width: "min(420px, 92vw)" }}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <h3 style={{ fontSize: 20, margin: "0 0 8px" }}>Админ-панель</h3>
        <p style={{ color: "var(--text-secondary)", fontSize: 14, margin: "0 0 16px" }}>
          Вход по internal-токену (X-Internal-Token).
        </p>
        <input
          className="input"
          type="password"
          placeholder="Internal token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoFocus
        />
        {error && <p style={{ color: "var(--destructive)", fontSize: 14 }}>{error}</p>}
        <button
          className="btn btn--primary"
          style={{ width: "100%", marginTop: 12 }}
          disabled={!token || busy}
        >
          {busy ? "Проверяем…" : "Войти"}
        </button>
      </form>
    </div>
  );
}

const SIDE_ITEMS = [
  { to: "/admin/sources", icon: Activity, text: "Источники" },
  { to: "/admin/partners", icon: Users, text: "Партнёры" },
  { to: "/admin/leads", icon: Car, text: "Лиды" },
];

export function AdminLayout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return (
    <div className="pa-shell">
      <nav className="pa-side">
        <div className="pa-side__logo">⚙️ Админка</div>
        {SIDE_ITEMS.map((it) => (
          <Link
            key={it.to}
            to={it.to}
            className={
              location.pathname.startsWith(it.to)
                ? "sidebar-item sidebar-item--active"
                : "sidebar-item"
            }
          >
            <it.icon size={18} />
            {it.text}
          </Link>
        ))}
        <div style={{ flex: 1 }} />
        <button
          className="btn"
          style={{ margin: "0 12px" }}
          onClick={() => {
            clearAdminToken();
            window.location.reload();
          }}
        >
          Выйти
        </button>
      </nav>
      <main className="pa-main">{children}</main>
      <ChatWidget />
    </div>
  );
}

export function AdminApp() {
  const [hasToken, setHasToken] = useState(() => Boolean(getAdminToken()));
  const [error, setError] = useState<string | null>(null);
  const [partners, setPartners] = useState<AdminPartner[] | null>(null);

  const onUnauthorized = useCallback(() => {
    clearAdminToken();
    setHasToken(false);
  }, []);

  useEffect(() => {
    if (!hasToken) return;
    aapi<AdminPartner[]>("/partners")
      .then(setPartners)
      .catch((e) => {
        if (e instanceof AdminApiError && e.status === 401) onUnauthorized();
        else setError((e as Error).message);
      });
  }, [hasToken, onUnauthorized]);

  if (!hasToken) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/admin" element={<LoginScreen onAuthed={() => setHasToken(true)} />} />
          <Route path="/admin/*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </BrowserRouter>
    );
  }

  if (error) {
    return (
      <AdminLayout>
        <ErrorState description={error} onRetry={() => setError(null)} />
      </AdminLayout>
    );
  }

  return (
    <AdminLayout>
      <Routes>
        <Route path="/admin" element={<Navigate to="/admin/sources" replace />} />
        <Route path="/admin/sources" element={<Sources />} />
        <Route
          path="/admin/partners"
          element={
            <Partners
              partners={partners ?? []}
              onUnauthorized={onUnauthorized}
              onChanged={() => {
                aapi<AdminPartner[]>("/partners").then(setPartners).catch(() => undefined);
              }}
            />
          }
        />
        <Route path="/admin/leads" element={<AdminLeads onUnauthorized={onUnauthorized} />} />
        <Route path="*" element={<Navigate to="/admin/sources" replace />} />
      </Routes>
    </AdminLayout>
  );
}
