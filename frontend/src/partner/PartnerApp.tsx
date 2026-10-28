/**
 * Партнёрский кабинет `/partner` — редизайн по docs/REDESIGN/04-partner-redesign.md:
 * - вход по API-ключу с маской и «Запомнить на этом устройстве» (X5, §4.7);
 * - десктоп sidebar со всеми 9 разделами / мобильный таб-бар 5:
 *   Обзор, Пул, Мои, Чаты, «Ещё» → bottom-sheet остальных разделов
 *   (NAV-V2 §3; активный из группы «Ещё» подсвечивает таб точкой) + меню
 *   «⋯» шапки для Ключа API и выхода (N1, §3.2);
 * - бейджи: новые лиды на «Пуле», «ждут отметки» на «Моих лидах», точка
 *   низкого баланса на «Биллинге» (X1/M4);
 * - фоновый poll 60 c при активной вкладке + маячок в document.title (§5.1);
 * - 401 → экран входа с баннером «ключ отозван» (X4), выход без reload (N3).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Check, ClipboardCheck, CreditCard, KeyRound, LayoutDashboard, ListChecks, Megaphone, Menu, MessageCircle, Store, TrendingUp, Users } from "lucide-react";

import { applyTheme, watchSystemTheme } from "../lib/theme";
import "./partner.css";

import {
  PartnerApiError,
  clearPartnerKey,
  fetchPartnerChats,
  getPartnerKey,
  papi,
  partnerFail,
  planTitle,
  setPartnerKey,
  type PartnerLead,
  type PartnerMe,
} from "./api";
import {
  ErrorBlock,
  Sheet,
  SkelRows,
  TabBadge,
  fmtMoney,
  fmtUsd,
  leadsPlural,
  qualParts,
} from "./components/bits";
import { PToastProvider, usePToast } from "./components/toast";
import {
  ApiKeyScreen,
  Billing,
  Market,
  MyLeads,
  Overview,
  PartnerAds,
  PartnerChats,
  Pool,
  Showcase,
} from "./PartnerScreens";
import { PartnerInspections } from "./PartnerInspections";

const POLL_MS = 60_000; // X1: лёгкий poll каждые 60 c
const BASE_TITLE = "Кабинет партнёра";

// ---------- Вход по ключу (§4.7) ----------

function LoginScreen({
  onAuthed,
  revoked,
}: {
  onAuthed: () => void;
  /** Показ true после принудительного выхода по 401 — баннер-причина (X4). */
  revoked?: boolean;
}) {
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!key.trim() || busy) return;
    setBusy(true);
    setError(null);
    setPartnerKey(key.trim(), remember);
    try {
      await papi<PartnerMe>("/me");
      onAuthed();
    } catch (e) {
      clearPartnerKey();
      if (e instanceof PartnerApiError && e.status === 401) {
        setError("Неверный или отозванный ключ. Запросите новый у менеджера.");
      } else {
        setError("Сервис недоступен. Проверьте интернет и попробуйте позже.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pa-login">
      <form
        className="pa-login__panel"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <h1 className="pa-login__title">Кабинет партнёра</h1>
        <p className="pa-login__desc">
          Войдите с API-ключом, выданным менеджером (начинается с <code>pi_</code>).
        </p>
        {revoked && (
          <div className="pa-login__banner" role="alert">
            Ключ отозван или сменён — введите новый.
          </div>
        )}
        <div className="pa-login__keyrow">
          <input
            className="input"
            type={showKey ? "text" : "password"}
            placeholder="pi_…"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoFocus
            autoComplete="off"
            aria-label="API-ключ партнёра"
          />
          <button
            type="button"
            className="pa-login__show"
            aria-label={showKey ? "Скрыть ключ" : "Показать ключ"}
            onClick={() => setShowKey(!showKey)}
          >
            {showKey ? "Скрыть" : "Показать"}
          </button>
        </div>
        <label className="pa-login__remember">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          Запомнить на этом устройстве
        </label>
        {error && (
          <p className="pa-login__error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn--primary pa-login__submit" disabled={!key.trim() || busy}>
          {busy ? "Проверяем…" : "Войти"}
        </button>
      </form>
    </div>
  );
}

// ---------- Навигация (§3.2): порядок по частоте сценария (N2) ----------

interface NavItem {
  to: string;
  label: string;
  tabLabel: string;
  icon: typeof LayoutDashboard;
  end?: boolean;
  badge?: "new" | "pending" | "lowBalance" | "unread";
}

// Порядок по частоте сценария (N2); «Чаты» — между «Моими лидами» и «Рынком»
// (05 §7), «Витрина» — после «Рынка»; «Объявления» (06 §6, размещения за
// слоты пакетов) — сразу после Витрины.
const NAV_ITEMS: NavItem[] = [
  { to: "/partner", label: "Обзор", tabLabel: "Обзор", icon: LayoutDashboard, end: true },
  { to: "/partner/pool", label: "Пул лидов", tabLabel: "Пул", icon: Users, badge: "new" },
  { to: "/partner/leads", label: "Мои лиды", tabLabel: "Мои", icon: ListChecks, badge: "pending" },
  { to: "/partner/chats", label: "Чаты", tabLabel: "Чаты", icon: MessageCircle, badge: "unread" },
  { to: "/partner/market", label: "Рынок", tabLabel: "Рынок", icon: TrendingUp },
  { to: "/partner/showcase", label: "Витрина", tabLabel: "Витрина", icon: Store },
  { to: "/partner/ads", label: "Объявления", tabLabel: "Объявления", icon: Megaphone },
  { to: "/partner/inspections", label: "Чеки и история", tabLabel: "Чеки", icon: ClipboardCheck },
  { to: "/partner/billing", label: "Биллинг", tabLabel: "Биллинг", icon: CreditCard, badge: "lowBalance" },
];

// Таб-бар (мобайл, NAV-V2 §3): первые 4 раздела по частоте сценария (N2);
// остальные 5 сворачиваются в таб «Ещё» с bottom-sheet — порядок в
// NAV_ITEMS и есть порядок в сайдбаре/шторке, поэтому просто срезы.
const TAB_ITEMS: NavItem[] = NAV_ITEMS.slice(0, 4);
/** Группа «Ещё» (bottom-sheet): рынок → биллинг (NAV-V2 §3). */
const MORE_ITEMS: NavItem[] = NAV_ITEMS.slice(4);

// ---------- Оболочка: sidebar (десктоп) + таб-бар (мобайл, N1) ----------

function Layout({
  me,
  newLeads,
  pendingCount,
  unreadChats,
  onLogout,
  children,
}: {
  me: PartnerMe;
  newLeads: number;
  pendingCount: number;
  /** Непрочитанные в чатах счёта (poll GET /api/partner/chats, 05 §7). */
  unreadChats: number;
  onLogout: () => void;
  children: React.ReactNode;
}) {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  /** Шторка «Ещё» (мобайл, NAV-V2 §3): рынок/витрина/объявления/чеки/биллинг. */
  const [moreOpen, setMoreOpen] = useState(false);
  const lowBalance = me.cpl_cost > 0 && me.balance < 3 * me.cpl_cost;

  // Активный раздел группы «Ещё»: подсвечивает таб и получает галочку в шторке
  const moreNow = MORE_ITEMS.find(
    (i) => location.pathname === i.to || location.pathname.startsWith(`${i.to}/`),
  );

  const badgeOf = (item: NavItem): { count?: number; dot?: boolean } => {
    if (item.badge === "new") return { count: newLeads };
    if (item.badge === "pending") return { count: pendingCount };
    if (item.badge === "unread") return { count: unreadChats };
    if (item.badge === "lowBalance") return { dot: lowBalance };
    return {};
  };

  // Меню «⋯» и шторка «Ещё» закрываются при уходе с экрана; Esc — в эффектах
  useEffect(() => {
    setMenuOpen(false);
    setMoreOpen(false);
  }, [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="pa-app">
      {/* Десктоп ≥768: sidebar */}
      <aside className="pa-sidebar">
        <div className="pa-sidebar__logo">Кабинет партнёра</div>
        <nav className="pa-sidebar__nav" aria-label="Разделы кабинета">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? "pa-navitem pa-navitem--active" : "pa-navitem"
              }
            >
              <item.icon size={18} aria-hidden="true" />
              <span>{item.label}</span>
              <TabBadge {...badgeOf(item)} />
            </NavLink>
          ))}
        </nav>
        <div className="pa-sidebar__foot">
          <NavLink
            to="/partner/key"
            className={({ isActive }) =>
              isActive ? "pa-navitem pa-navitem--active" : "pa-navitem"
            }
          >
            <KeyRound size={18} aria-hidden="true" />
            <span>Ключ API</span>
          </NavLink>
          <button type="button" className="pa-navitem pa-navitem--btn" onClick={onLogout}>
            <span aria-hidden="true">⎋</span>
            <span>Выйти</span>
          </button>
        </div>
      </aside>

      <div className="pa-body">
        {/* Шапка контента (§3.2): имя · план · баланс — всегда на виду (В1) */}
        <header className="pa-topbar">
          <div className="pa-topbar__id">
            <span className="pa-topbar__name">{me.name}</span>
            <span className="pa-pill">{planTitle(me.plan)}</span>
          </div>
          <div className="pa-topbar__right">
            <span
              className="pa-topbar__balance"
              title={`Хватит на ${me.cpl_cost > 0 ? Math.floor(me.balance / me.cpl_cost) : 0} ${leadsPlural(me.cpl_cost > 0 ? Math.floor(me.balance / me.cpl_cost) : 0)}`}
            >
              Баланс: <b>{fmtUsd(me.balance)}</b>
            </span>
            {/* Меню «⋯» (мобайл): Ключ API и Выход не занимают таб-бар (N1);
                имя «Меню» — чтобы не дублировать таб «Ещё» (NAV-V2 §3) */}
            <button
              type="button"
              className="pa-topbar__menu"
              aria-label="Меню"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(!menuOpen)}
            >
              ⋯
            </button>
            {menuOpen && (
              <div className="pa-menu" role="menu">
                <Link className="pa-menu__item" role="menuitem" to="/partner/key">
                  Ключ API
                </Link>
                <button type="button" className="pa-menu__item" role="menuitem" onClick={onLogout}>
                  Выйти
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="pa-content" id="pa-main">{children}</main>
      </div>

      {/* Мобайл <768: таб-бар 5 — Обзор, Пул, Мои, Чаты, «Ещё» (NAV-V2 §3) */}
      <nav className="pa-tabbar" aria-label="Разделы кабинета">
        {TAB_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              isActive ? "pa-tab pa-tab--active" : "pa-tab"
            }
            aria-label={item.label}
          >
            <item.icon size={20} aria-hidden="true" />
            <span className="pa-tab__label">{item.tabLabel}</span>
            <TabBadge {...badgeOf(item)} />
          </NavLink>
        ))}
        {/* «Ещё»: открывает bottom-sheet остальных разделов; активный путь
            внутри группы подсвечивает таб и ставит точку (NAV-V2 §3) */}
        <button
          type="button"
          className={moreNow ? "pa-tab pa-tab--active" : "pa-tab"}
          aria-label="Ещё"
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(true)}
        >
          <Menu size={20} aria-hidden="true" />
          <span className="pa-tab__label">Ещё</span>
          {moreNow && <span className="pa-tab__dot" aria-hidden="true" />}
        </button>
      </nav>

      {/* Шторка «Ещё» (NAV-V2 §3): рынок/витрина/объявления/чеки/биллинг;
          закрытие — выбором, по scrim и Esc (Sheet, 02 §7.10) */}
      <Sheet open={moreOpen} title="Ещё" onClose={() => setMoreOpen(false)}>
        <nav className="pa-more" aria-label="Разделы кабинета">
          {MORE_ITEMS.map((item) => {
            const current = moreNow?.to === item.to;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={current ? "pa-more__item pa-more__item--active" : "pa-more__item"}
                aria-current={current ? "page" : undefined}
                onClick={() => setMoreOpen(false)}
              >
                <item.icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
                <span className="pa-more__trail">
                  <TabBadge {...badgeOf(item)} />
                  {/* текущий раздел внутри шторки — галочка (NAV-V2 §3) */}
                  {current && (
                    <span className="pa-more__check" aria-hidden="true">
                      <Check size={16} />
                    </span>
                  )}
                </span>
              </Link>
            );
          })}
        </nav>
      </Sheet>
    </div>
  );
}

// ---------- Корень ----------

type Phase = "boot" | "login" | "ready";

function PartnerShell() {
  const toast = usePToast();
  const nav = useNavigate();
  const location = useLocation();

  const [phase, setPhase] = useState<Phase>(() => (getPartnerKey() ? "boot" : "login"));
  const [me, setMe] = useState<PartnerMe | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  /** Баннер на входе после 401 «ключ отозван» (X4). */
  const [revoked, setRevoked] = useState(false);
  const [newLeads, setNewLeads] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  /** Непрочитанные в чатах счёта — бейдж раздела «Чаты» (05 §7). */
  const [unreadChats, setUnreadChats] = useState(0);
  /** Граница «виденного» пула: всё новее — кандидаты в бейдж (X1). */
  const lastSeenRef = useRef(Date.now());
  const newLeadsRef = useRef(0);

  // Тема: системная light/dark, без Telegram-пары (Д2)
  useEffect(() => {
    applyTheme();
    return watchSystemTheme(() => undefined);
  }, []);

  // Маячок вкладки (§5.1): ● N — Кабинет партнёра
  useEffect(() => {
    document.title = newLeads > 0 ? `● ${newLeads} — ${BASE_TITLE}` : BASE_TITLE;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [newLeads]);

  /** 401 в любом запросе: ключ отозван/сменён → вход с баннером (X4). */
  const onUnauthorized = useCallback(() => {
    clearPartnerKey();
    setMe(null);
    setNewLeads(0);
    newLeadsRef.current = 0;
    setPendingCount(0);
    setUnreadChats(0);
    setRevoked(true);
    setPhase("login");
  }, []);

  const loadMe = useCallback(
    (onDone?: () => void) => {
      papi<PartnerMe>("/me")
        .then((m) => {
          // Гонка с выходом: запрос ушёл до «Выйти», ответ пришёл после —
          // ключа уже нет, кабинет по позднему /me не открываем.
          if (!getPartnerKey()) return;
          setMe(m);
          setPhase("ready");
          setBootError(null);
          onDone?.();
        })
        .catch((e) => {
          if (e instanceof PartnerApiError && e.status === 401) {
            onUnauthorized();
            return;
          }
          if (getPartnerKey()) {
            setBootError(partnerFail(e).text);
            setPhase("ready"); // кабинет с баннером ошибки и «Повторить»
          }
          onDone?.();
        });
    },
    [onUnauthorized],
  );

  /** «Ждут отметки»: claimed без фидбека (M4). */
  const refreshPending = useCallback(() => {
    papi<PartnerLead[]>("/leads?mine=true&limit=100")
      .then((mine) =>
        setPendingCount(
          mine.filter((l) => (l.partner_status ?? "claimed") === "claimed").length,
        ),
      )
      .catch(() => undefined);
  }, []);

  /** Бейдж «Чаты»: сумма unread тредов счёта (poll 60 c, 05 §7). */
  const refreshChats = useCallback(() => {
    fetchPartnerChats()
      .then((threads) => {
        // партнёр вышел, пока запрос был в полёте — бейдж не воскрешаем
        if (!getPartnerKey()) return;
        setUnreadChats(threads.reduce((acc, t) => acc + (t.unread ?? 0), 0));
      })
      .catch(() => undefined); // 401 поднимет другой poll (onUnauthorized)
  }, []);

  // Стартовая загрузка /me (boot-фаза)
  useEffect(() => {
    if (phase === "boot") loadMe();
  }, [phase, loadMe]);

  // Бейдж чатов: сразу после входа, дальше — общий poll 60 c
  useEffect(() => {
    if (phase === "ready") refreshChats();
  }, [phase, refreshChats]);

  // Первый заход в пул сбрасывает бейдж новых (§5.1)
  useEffect(() => {
    if (location.pathname === "/partner/pool") {
      lastSeenRef.current = Date.now();
      if (newLeadsRef.current !== 0) {
        newLeadsRef.current = 0;
        setNewLeads(0);
      }
    }
  }, [location.pathname]);

  // Фоновый poll (X1, §5.1): 60 c при активной вкладке; /me — раз в 5 тиков
  useEffect(() => {
    if (phase !== "ready") return;
    let tick = 0;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      tick += 1;
      papi<PartnerLead[]>("/leads?limit=10")
        .then((leads) => {
          // Партнёр вышел, пока запрос был в полёте: без ключа бейдж и
          // тост «Новый лид» не показываем (в том числе на экране входа).
          if (!getPartnerKey()) return;
          const fresh = leads.filter((l) => Date.parse(l.created_at) > lastSeenRef.current);
          if (fresh.length > newLeadsRef.current) {
            newLeadsRef.current = fresh.length;
            setNewLeads(fresh.length);
            const budget = qualParts(fresh[0].qualification).budget;
            toast.show(
              budget != null ? `Новый лид в пуле: бюджет ${fmtMoney(budget, "$")}` : "Новый лид в пуле",
              { action: { label: "Открыть пул", onClick: () => nav("/partner/pool") } },
            );
          } else if (fresh.length !== newLeadsRef.current) {
            // лиды разобрали другие партнёры — честно уменьшаем бейдж
            newLeadsRef.current = fresh.length;
            setNewLeads(fresh.length);
          }
        })
        .catch(() => undefined);
      // Непрочитанные чатов — каждый тик (60 c, 05 §7)
      refreshChats();
      if (tick % 5 === 0) {
        loadMe();
        refreshPending();
      }
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [phase, toast, nav, loadMe, refreshPending, refreshChats]);

  /** Экраны зовут после claim/фидбека: обновить /me и бейджи сразу. */
  const onChanged = useCallback(() => {
    loadMe();
    refreshPending();
  }, [loadMe, refreshPending]);

  const onLogout = useCallback(() => {
    clearPartnerKey(); // без window.location.reload() — SPA остаётся живым (N3)
    setMe(null);
    setNewLeads(0);
    newLeadsRef.current = 0;
    setPendingCount(0);
    setUnreadChats(0);
    setRevoked(false);
    setPhase("login");
  }, []);

  if (phase === "login") {
    return (
      <LoginScreen
        revoked={revoked}
        onAuthed={() => {
          lastSeenRef.current = Date.now(); // отсчёт «новых» с момента входа
          setRevoked(false);
          refreshPending();
          setPhase("boot");
        }}
      />
    );
  }

  if (!me) {
    // /me не пришёл (сеть/5xx): ошибка с «Повторить» вместо вечного скелетона
    if (bootError) {
      return (
        <div className="pa-boot">
          <ErrorBlock description={bootError} actionLabel="Повторить" onAction={() => loadMe()} />
        </div>
      );
    }
    return (
      <div className="pa-boot" aria-busy="true" aria-label="Загружаем кабинет">
        <SkelRows n={6} />
      </div>
    );
  }

  return (
    <Layout
      me={me}
      newLeads={newLeads}
      pendingCount={pendingCount}
      unreadChats={unreadChats}
      onLogout={onLogout}
    >
      <Routes>
        <Route path="/partner" element={<Overview me={me} onUnauthorized={onUnauthorized} onChanged={onChanged} />} />
        <Route path="/partner/pool" element={<Pool me={me} onUnauthorized={onUnauthorized} onChanged={onChanged} />} />
        <Route path="/partner/leads" element={<MyLeads me={me} onUnauthorized={onUnauthorized} onChanged={onChanged} />} />
        {/* /partner/chats/* — список + overlay-чат /partner/chats/:id (05 §7) */}
        <Route path="/partner/chats/*" element={<PartnerChats onUnauthorized={onUnauthorized} />} />
        <Route path="/partner/market" element={<Market onUnauthorized={onUnauthorized} />} />
        <Route path="/partner/showcase" element={<Showcase onUnauthorized={onUnauthorized} />} />
        {/* Объявления (06 §6): пакеты размещений + свои лоты рынка */}
        <Route path="/partner/ads" element={<PartnerAds onUnauthorized={onUnauthorized} onChanged={onChanged} />} />
        {/* /partner/inspections — конструктор чеков осмотра + история машины (07 §3) */}
        <Route path="/partner/inspections" element={<PartnerInspections onUnauthorized={onUnauthorized} />} />
        <Route path="/partner/billing" element={<Billing onUnauthorized={onUnauthorized} />} />
        <Route path="/partner/key" element={<ApiKeyScreen me={me} />} />
        <Route path="*" element={<Navigate to="/partner" replace />} />
      </Routes>
    </Layout>
  );
}

export function PartnerApp() {
  return (
    <PToastProvider>
      <BrowserRouter>
        <PartnerShell />
      </BrowserRouter>
    </PToastProvider>
  );
}
