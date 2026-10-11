/** Корневой компонент: авторизация, онбординг, таб-навигация (ТЗ п. 9.2.9). */

import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { AppRoot, Placeholder, Tabbar } from "@telegram-apps/telegram-ui";

import { authorize } from "./api/auth";
import { getAccessToken } from "./api/client";
import { LoginScreen } from "./screens/Login";
import { FeedScreen } from "./screens/Feed";
import { ListingScreen } from "./screens/Listing";
import { FiltersScreen } from "./screens/Filters";
import { AiSearchScreen } from "./screens/AiSearch";
import { FavoritesScreen } from "./screens/Favorites";
import { ProfileScreen } from "./screens/Profile";
import { OnboardingScreen } from "./screens/Onboarding";

type AuthState = "loading" | "ready";

function NavBar() {
  const navigate = useNavigate();
  const items = [
    { path: "/", icon: "🏠", text: "Лента" },
    { path: "/ai", icon: "🤖", text: "AI" },
    { path: "/filters", icon: "🔍", text: "Фильтры" },
    { path: "/favorites", icon: "❤️", text: "Избранное" },
    { path: "/profile", icon: "👤", text: "Профиль" },
  ];
  return (
    <Tabbar style={{ position: "fixed", bottom: 0, left: 0, right: 0 }}>
      {items.map((it) => (
        <Tabbar.Item key={it.path} text={it.text} selected={location.pathname === it.path} onClick={() => navigate(it.path)}>
          {it.icon}
        </Tabbar.Item>
      ))}
    </Tabbar>
  );
}

export function App() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [onboarded, setOnboarded] = useState(
    () => sessionStorage.getItem("autobot.onboarded") === "1",
  );
  const [authed, setAuthed] = useState(() => Boolean(getAccessToken()));

  useEffect(() => {
    authorize()
      .then((r) => {
        if (r) setAuthed(true);
      })
      .catch(() => undefined) // вне Telegram — покажем форму входа
      .finally(() => setAuth("ready"));
  }, []);

  if (auth === "loading") {
    return (
      <AppRoot>
        <Placeholder description="Загрузка…" />
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
        <OnboardingScreen
          onDone={() => {
            sessionStorage.setItem("autobot.onboarded", "1");
            setOnboarded(true);
          }}
        />
      </AppRoot>
    );
  }

  return (
    <AppRoot>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<FeedScreen />} />
          <Route path="/listing/:id" element={<ListingScreen />} />
          <Route path="/ai" element={<AiSearchScreen />} />
          <Route path="/filters" element={<FiltersScreen />} />
          <Route path="/favorites" element={<FavoritesScreen />} />
          <Route path="/profile" element={<ProfileScreen />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <NavBar />
      </BrowserRouter>
    </AppRoot>
  );
}
