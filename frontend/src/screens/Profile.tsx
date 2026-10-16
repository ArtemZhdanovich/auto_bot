/** Профиль (ТЗ п. 9.2.7): пользователь, тариф, оплата подписки Stars (этап 6). */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Cell, Placeholder, Section } from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import { haptic, isGuest, logout, tgUser } from "../api/auth";
import type { StarsInvoice, Subscription, User } from "../api/types";

const TIER_LABEL: Record<string, string> = {
  free: "Free",
  pro: "Pro",
  business: "Business",
};

type InvoiceStatus = "paid" | "failed" | "pending";

/** tg.openInvoice с колбеком статуса (ТЗ п. 9.6). */
function openInvoice(url: string, onStatus: (s: InvoiceStatus) => void): void {
  const webApp = (window as unknown as { Telegram?: { WebApp?: { openInvoice?: (u: string, cb: (s: string) => void) => void } } }).Telegram?.WebApp;
  if (webApp?.openInvoice) {
    webApp.openInvoice(url, (status) => onStatus(status as InvoiceStatus));
  } else {
    window.open(url, "_blank"); // вне Telegram — просто открываем ссылку
    onStatus("pending");
  }
}

export function ProfileScreen() {
  const [user, setUser] = useState<User | null>(null);
  const [sub, setSub] = useState<Subscription | null>(null);
  const [buying, setBuying] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<User>("/me")
      .then(setUser)
      .catch((e: Error) => setError(e.message));
    api<Subscription>("/subscription")
      .then(setSub)
      .catch(() => undefined); // вне Telegram статуса нет — read-only
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const buy = async (tier: "pro" | "business", period: "month" | "year") => {
    const key = `${tier}_${period}`;
    setBuying(key);
    setMessage(null);
    try {
      const invoice = await api<StarsInvoice>("/payments/stars", {
        method: "POST",
        body: JSON.stringify({ tier, period }),
      });
      haptic();
      openInvoice(invoice.invoice_url, (status) => {
        setBuying(null);
        if (status === "paid") {
          setMessage("✅ Оплата прошла — подписка активирована");
          load();
        } else if (status === "failed") {
          setMessage("❌ Оплата не прошла");
        }
      });
    } catch (e) {
      setMessage(`Ошибка: ${(e as Error).message}`);
      setBuying(null);
    }
  };

  const tg = tgUser();

  if (error) {
    return <Placeholder header="Нужна авторизация" description="Откройте приложение из Telegram-бота" />;
  }
  if (!user) {
    return <Placeholder description="Загрузка…" />;
  }

  const until = sub?.subscription_until
    ? new Date(sub.subscription_until).toLocaleDateString("ru")
    : null;

  const guest = isGuest();

  return (
    <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "12px" }}>
      {guest && (
        <Section header="Гостевой режим">
          <div style={{ padding: "0 12px 8px", fontSize: 13, lineHeight: 1.5, opacity: 0.75 }}>
            Вы смотрите как гость: лента, AI-разбор цены (1 бесплатный), калькулятор и
            избранное доступны. Сохранённые машины и уведомления в Telegram появятся
            после входа.
          </div>
          <div style={{ display: "flex", gap: 8, padding: 12 }}>
            <Link to="/login" style={{ flex: 1, textDecoration: "none" }}>
              <Button stretched mode="filled">Войти / Регистрация</Button>
            </Link>
          </div>
        </Section>
      )}
      <Section header="Профиль">
        <Cell
          subtitle={user.username ? `@${user.username}` : undefined}
          after={<b>{TIER_LABEL[user.subscription_tier]}</b>}
        >
          {user.first_name} {user.last_name ?? ""}
        </Cell>
        <Cell subtitle="Роль">{user.role}</Cell>
        <Cell subtitle="С нами с">
          {new Date(user.created_at).toLocaleDateString("ru")}
        </Cell>
      </Section>
      <Section header="Подписка">
        <Cell
          after={<b>{TIER_LABEL[sub?.tier ?? user.subscription_tier]}</b>}
          subtitle={until ? `активна до ${until}` : "бесплатный тариф"}
        >
          Текущий тариф
        </Cell>
        {sub && (
          <Cell description={`${sub.max_filters ?? "∞"} фильтров · ${sub.ai_daily_limit ?? "∞"} AI-сообщений/день · instant: ${sub.instant ? "да" : "нет"}`}>
            Лимиты тарифа
          </Cell>
        )}
        {sub?.tier !== "pro" && (
          <Button
            loading={buying === "pro_month"}
            onClick={() => buy("pro", "month")}
          >
            ⭐ Pro — 500★/мес
          </Button>
        )}
        {sub?.tier !== "pro" && (
          <Button mode="outline" loading={buying === "pro_year"} onClick={() => buy("pro", "year")}>
            ⭐ Pro — 4800★/год (−20%)
          </Button>
        )}
        {sub?.tier !== "business" && (
          <Button
            mode="outline"
            loading={buying === "business_month"}
            onClick={() => buy("business", "month")}
          >
            ⭐ Business — 2500★/мес
          </Button>
        )}
        {message && <Cell subtitle={message}>{""}</Cell>}
      </Section>
      <Button mode="outline" onClick={logout}>
        Выйти (сбросить сессию)
      </Button>
      {tg && (
        <Cell subtitle={`Telegram ID: ${tg.id}`} description="Данные получены из Telegram">
          Контекст Mini App активен
        </Cell>
      )}
    </div>
  );
}
