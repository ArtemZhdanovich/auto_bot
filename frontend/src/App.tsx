/** Корневой компонент: авторизация, онбординг, навигация (UI/UX ТЗ §2.1). */

import { useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { AppRoot, Tabbar } from "@telegram-apps/telegram-ui";
import { Home, Heart, SlidersHorizontal, Sparkles, User } from "lucide-react";

import { authorize, guestLogin, isGuest, clearGuest } from "./api/auth";
import { api, getAccessToken } from "./api/client";
import { LoginScreen } from "./screens/Login";
import { FeedScreen } from "./screens/Feed";
import { ListingScreen } from "./screens/Listing";
import { FiltersScreen } from "./screens/Filters";
import { AiSearchScreen } from "./screens/AiSearch";
import { CalcScreen } from "./screens/Calc";
import { CompareScreen } from "./screens/Compare";
import { FavoritesScreen } from "./screens/Favorites";
import { ProfileScreen } from "./screens/Profile";
import { OnboardingScreen } from "./screens/Onboarding";
import { ToastProvider } from "./ui/Toast";
import { LoginPromptProvider } from "./ui/LoginPrompt";
import { PartnerApp } from "./partner/PartnerApp";
import { AdminApp } from "./admin/AdminApp";
import type { User as UserT } from "./api/types";

/** Отдельные поверхности: партнёрский ЛК и админка — вне Telegram-гейта. */
function SurfaceRouter() {
  const path = window.location.pathname;
  if (path.startsWith("/partner")) {
    return (
      <ToastProvider>
        <PartnerApp />
      </ToastProvider>
    );
  }
  if (path.startsWith("/admin")) {
    return (
      <ToastProvider>
        <AdminApp />
      </ToastProvider>
    );
  }
  return null;
}


type AuthState = "loading" | "ready";

const NAV_ITEMS = [
  { path: "/", icon: Home, text: "Лента" },
  { path: "/ai", icon: Sparkles, text: "AI" },
  { path: "/filters", icon: SlidersHorizontal, text: "Фильтры" },
  { path: "/favorites", icon: Heart, text: "Избранное" },
  { path: "/profile", icon: User, text: "Профиль" },
];

/** Мобильный таббар (SVG-иконки) / десктопный sidebar (>= 768px, ТЗ §2.1). */
function NavBar({ desktop }: { desktop: boolean }) {
  const navigate = useNavigate();
  if (desktop) {
    return (
      <nav className="app-sidebar">
        {NAV_ITEMS.map((it) => (
          <div
            key={it.path}
            className={
              location.pathname === it.path ? "sidebar-item sidebar-item--active" : "sidebar-item"
            }
            onClick={() => navigate(it.path)}
          >
            <it.icon size={20} />
            {it.text}
          </div>
        ))}
      </nav>
    );
  }
  return (
    <Tabbar className="app-tabbar" style={{ position: "fixed", bottom: 0, left: 0, right: 0 }}>
      {NAV_ITEMS.map((it) => (
        <Tabbar.Item
          key={it.path}
          text={it.text}
          selected={location.pathname === it.path}
          onClick={() => navigate(it.path)}
        >
          <it.icon size={24} />
        </Tabbar.Item>
      ))}
    </Tabbar>
  );
}

/** Реакция на ширину окна для mobile/desktop переключения. */
function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(() => window.innerWidth >= 768);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return isDesktop;
}

/** Точка входа: партнёрский ЛК и админка — свои поверхности без Telegram-авторизации. */
export function App() {
  if (
    window.location.pathname.startsWith("/partner") ||
    window.location.pathname.startsWith("/admin")
  ) {
    return <SurfaceRouter />;
  }
  return <MiniApp />;
}

function MiniApp() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [onboarded, setOnboarded] = useState<boolean | null>(null);
  const [authed, setAuthed] = useState(() => Boolean(getAccessToken()));
  const [guest, setGuest] = useState(() => isGuest());
  const [bannerHidden, setBannerHidden] = useState(
    () => sessionStorage.getItem("autobot.guestBanner") === "1",
  );
  const isDesktop = useIsDesktop();

  useEffect(() => {
    authorize()
      .then(async (r) => {
        if (r) {
          setAuthed(true);
          return;
        }
        // Вне Telegram — гостевой вход: даём потрогать демо без регистрации
        // (лента, карточка, AI-разбор 1/нед, калькулятор, избранное).
        try {
          await guestLogin();
          setAuthed(true);
          setGuest(true);
        } catch {
          // backend недоступен — покажем форму входа как раньше
        }
      })
      .catch(() => undefined)
      .finally(() => setAuth("ready"));
  }, []);

  // Онбординг-флаг из БД (users.onboarded_at, ТЗ §2.8); без БД — раз в сессию.
  useEffect(() => {
    if (!authed || onboarded !== null) return;
    if (guest) {
      setOnboarded(true); // гостю онбординг не нужен — сразу демо ленты
      return;
    }
    if (sessionStorage.getItem("autobot.onboarded") === "1") {
      setOnboarded(true);
      return;
    }
    api<UserT>("/me")
      .then((u) => setOnboarded(u.onboarded_at !== null))
      .catch(() => setOnboarded(true)); // /me недоступен — не блокируем UI
  }, [authed, guest, onboarded]);

  const finishOnboarding = (_data: { role: string; countries: string[] }) => {
    // role/countries будут применены к фильтрам на следующих шагах (P4);
    // сейчас фиксируем только флаг онбординга (users.onboarded_at).
    api("/me/onboarded", { method: "POST" }).catch(() => undefined);
    sessionStorage.setItem("autobot.onboarded", "1");
    setOnboarded(true);
  };

  if (auth === "loading" || (authed && onboarded === null)) {
    return (
      <AppRoot>
        <div className="state" style={{ minHeight: "60vh" }}>
          <div className="state__title">Загрузка…</div>
        </div>
      </AppRoot>
    );
  }

  if (!authed) {
    return (
      <AppRoot>
        <LoginScreen onAuthed={() => setAuthed(true)} />
      </AppRoot>
    );
  }

  if (!onboarded) {
    return (
      <AppRoot>
        <OnboardingScreen onDone={finishOnboarding} />
      </AppRoot>
    );
  }

  return (
    <ToastProvider>
      <AppRoot>
        <div className={isDesktop ? "app-shell app-shell--desktop" : "app-shell"}>
          <BrowserRouter>
            {isDesktop && <NavBar desktop />}
            {guest && !bannerHidden && (
              <div className="guest-banner">
                <span>
                  👀 Демо без регистрации: смотрите, фильтруйте, пробуйте
                  AI-разбор цены (1 бесплатный).{" "}
                  <Link to="/login">Войти</Link>{" "}
                  — для сохранённых машин и уведомлений в Telegram.
                </span>
                <button
                  className="guest-banner__close"
                  aria-label="Скрыть"
                  onClick={() => {
                    sessionStorage.setItem("autobot.guestBanner", "1");
                    setBannerHidden(true);
                  }}
                >
                  ✕
                </button>
              </div>
            )}
            <div className="app-content">
              <LoginPromptProvider>
              <Routes>
                <Route path="/" element={<FeedScreen />} />
                <Route path="/listing/:id" element={<ListingScreen />} />
                <Route path="/calc" element={<CalcScreen />} />
                <Route path="/compare" element={<CompareScreen />} />
                <Route path="/ai" element={<AiSearchScreen />} />
                <Route path="/filters" element={<FiltersScreen />} />
                <Route path="/favorites" element={<FavoritesScreen />} />
                {guest ? (
                  <Route
                    path="/login"
                    element={
                      <LoginScreen
                        onAuthed={() => {
                          clearGuest();
                          setGuest(false);
                          setAuthed(true);
                        }}
                      />
                    }
                  />
                ) : null}
                <Route path="/profile" element={<ProfileScreen />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
              </LoginPromptProvider>
            </div>
            <NavBar desktop={false} />
          </BrowserRouter>
        </div>
      </AppRoot>
    </ToastProvider>
  );
}

