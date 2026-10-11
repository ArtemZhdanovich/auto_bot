/**
 * Партнёрский ЛК (UI/UX ТЗ §4): вход по API-ключу, layout, роутинг /partner/*.
 * Экраны — в PartnerScreens.tsx.
 */

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { CreditCard, KeyRound, LayoutDashboard, ListChecks, Users } from "lucide-react";

import {
  PartnerApiError,
  clearPartnerKey,
  getPartnerKey,
  papi,
  setPartnerKey,
  type PartnerMe,
} from "./api";
import {
  ApiKeyScreen,
  Billing,
  Dashboard,
  MyLeads,
  Pool,
} from "./PartnerScreens";
import { ErrorState } from "../ui/States";

// --- Вход по ключу (ТЗ §4.1: MVP без email/пароля) ---

function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setPartnerKey(key.trim());
    try {
      await papi<PartnerMe>("/me");
      onAuthed();
    } catch (e) {
      clearPartnerKey();
      setError(
        e instanceof PartnerApiError && e.status === 401
          ? "Неверный или отозванный ключ"
          : "Сервис недоступен, попробуйте позже",
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
        <h3 style={{ fontSize: 20, margin: "0 0 8px" }}>Кабинет партнёра</h3>
        <p style={{ color: "var(--text-secondary)", fontSize: 14, margin: "0 0 16px" }}>
          Войдите с API-ключом, выданным менеджером (начинается с <code>pi_</code>).
        </p>
        <input
          className="input"
          placeholder="pi_..."
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoFocus
        />
        {error && <p style={{ color: "var(--destructive)", fontSize: 14 }}>{error}</p>}
        <button
          className="btn btn--primary"
          style={{ width: "100%", marginTop: 12 }}
          disabled={!key || busy}
        >
          {busy ? "Проверяем…" : "Войти"}
        </button>
      </form>
    </div>
  );
}

// --- Layout: sidebar + контент (ТЗ §4.2) ---

const SIDE_ITEMS = [
  { to: "/partner", icon: LayoutDashboard, text: "Дашборд", end: true },
  { to: "/partner/pool", icon: Users, text: "Пул лидов" },
  { to: "/partner/leads", icon: ListChecks, text: "Мои лиды" },
  { to: "/partner/key", icon: KeyRound, text: "API-ключ" },
  { to: "/partner/billing", icon: CreditCard, text: "Биллинг" },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return (
    <div className="pa-shell">
      <nav className="pa-side">
        <div className="pa-side__logo">🚗 Auto Selection</div>
        {SIDE_ITEMS.map((it) => {
          const active = it.end
            ? location.pathname === it.to
            : location.pathname.startsWith(it.to);
          return (
            <Link
              key={it.to}
              to={it.to}
              className={active ? "sidebar-item sidebar-item--active" : "sidebar-item"}
            >
              <it.icon size={18} />
              {it.text}
            </Link>
          );
        })}
        <div style={{ flex: 1 }} />
        <button
          className="btn"
          style={{ margin: "0 12px" }}
          onClick={() => {
            clearPartnerKey();
            window.location.reload();
          }}
        >
          Выйти
        </button>
      </nav>
      <main className="pa-main">{children}</main>
    </div>
  );
}

// --- Корень ---

export function PartnerApp() {
  const [hasKey, setHasKey] = useState(() => Boolean(getPartnerKey()));
  const [me, setMe] = useState<PartnerMe | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onUnauthorized = useCallback(() => {
    clearPartnerKey();
    setHasKey(false);
  }, []);

  const reload = useCallback(() => {
    papi<PartnerMe>("/me")
      .then(setMe)
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError((e as Error).message);
      });
  }, [onUnauthorized]);

  useEffect(() => {
    if (hasKey) reload();
  }, [hasKey, reload]);

  if (!hasKey) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/partner" element={<LoginScreen onAuthed={() => setHasKey(true)} />} />
          <Route path="/partner/*" element={<Navigate to="/partner" replace />} />
        </Routes>
      </BrowserRouter>
    );
  }

  if (error) {
    return (
      <Layout>
        <ErrorState description={error} onRetry={reload} />
      </Layout>
    );
  }

  if (!me) {
    return (
      <Layout>
        <div className="panel">Загрузка…</div>
      </Layout>
    );
  }

  return (
    <Layout>
      <Routes>
        <Route path="/partner" element={<Dashboard me={me} />} />
        <Route
          path="/partner/pool"
          element={<Pool onUnauthorized={onUnauthorized} onClaimed={reload} />}
        />
        <Route path="/partner/leads" element={<MyLeads onUnauthorized={onUnauthorized} />} />
        <Route path="/partner/key" element={<ApiKeyScreen me={me} />} />
        <Route
          path="/partner/billing"
          element={<Billing onUnauthorized={onUnauthorized} />}
        />
        <Route path="*" element={<Navigate to="/partner" replace />} />
      </Routes>
    </Layout>
  );
}
