/** Профиль (ТЗ п. 9.2.7): пользователь, тариф, оплата подписки Stars (этап 6). */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { haptic, isGuest, logout, tgUser } from "../api/auth";
import {
  getRegion,
  setRegion,
  REGION_CURRENCY,
  REGION_LABEL,
  type Region,
} from "../lib/fx";
import type { StarsInvoice, Subscription, User } from "../api/types";

const TIER_LABEL: Record<string, string> = {
  free: "Free",
  pro: "Pro",
  business: "Business",
};

type InvoiceStatus = "paid" | "failed" | "cancelled" | "pending";

/** Внутри Telegram ли открыт мини-апп (оплата Stars работает только там). */
export function inTelegram(): boolean {
  return Boolean(
    (window as unknown as { Telegram?: { WebApp?: { initDataUnsafe?: unknown } } })
      .Telegram?.WebApp?.initDataUnsafe,
  );
}

/** tg.openInvoice с колбеком статуса (ТЗ п. 9.6). */
function openInvoice(url: string, onStatus: (s: InvoiceStatus) => void): boolean {
  const webApp = (window as unknown as { Telegram?: { WebApp?: { openInvoice?: (u: string, cb: (s: string) => void) => void } } }).Telegram?.WebApp;
  if (webApp?.openInvoice) {
    webApp.openInvoice(url, (status) => onStatus(status as InvoiceStatus));
    return true;
  }
  window.open(url, "_blank"); // вне Telegram — просто открываем ссылку
  return false;
}

export function ProfileScreen() {
  const [user, setUser] = useState<User | null>(null);
  const [sub, setSub] = useState<Subscription | null>(null);
  const [buying, setBuying] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [region, setRegionState] = useState<Region>(() => getRegion());

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
    let invoiceUrl: string | null = null;
    try {
      const invoice = await api<StarsInvoice>("/payments/stars", {
        method: "POST",
        body: JSON.stringify({ tier, period }),
      });
      invoiceUrl = invoice.invoice_url;
      haptic();
    } catch (e) {
      setBuying(null);
      const status = (e as { status?: number }).status;
      if (status === 503) {
        setMessage("🛠 Платежи временно недоступны — попробуйте через пару минут.");
      } else if (status === 401 || status === 403) {
        setMessage("🔒 Сессия истекла. Закройте мини-апп и откройте заново из Telegram-бота.");
      } else {
        setMessage(`Не удалось создать счёт: ${(e as Error).message}`);
      }
      return;
    }

    const inTg = openInvoice(invoiceUrl, (status) => {
      setBuying(null);
      if (status === "paid") {
        setMessage("✅ Оплата прошла — подписка активирована");
        load();
      } else if (status === "failed") {
        setMessage(
          "❌ Оплата не прошла. Проверьте баланс ★ Stars (Settings → Stars) и попробуйте ещё раз.",
        );
      } else if (status === "cancelled") {
        setMessage("Оплата отменена — инвойс можно открыть снова в любой момент.");
      } else {
        setMessage("⏳ Платёж в обработке — подписка активируется в течение пары минут.");
      }
    });
    if (!inTg) {
      setBuying(null);
      setMessage(
        "💳 Счёт открыт в новой вкладке, но оплата ★ Stars работает только в Telegram. Откройте мини-апп из бота и повторите оформление.",
      );
    }
  };

  const tg = tgUser();

  if (error) {
    return (
      <div className="screen">
        <div className="state">
          <div className="state__emoji">🔒</div>
          <div className="state__title">Нужна авторизация</div>
          <div className="state__text">Откройте приложение из Telegram-бота</div>
        </div>
      </div>
    );
  }
  if (!user) {
    return (
      <div className="screen">
        <div className="state"><span className="skeleton" style={{ width: 140, height: 16 }} /></div>
      </div>
    );
  }

  const until = sub?.subscription_until
    ? new Date(sub.subscription_until).toLocaleDateString("ru")
    : null;

  const guest = isGuest();

  return (
    <div className="screen">
      {guest && (
        <section className="panel profile-card">
          <h3>Гостевой режим</h3>
          <p className="profile-card__text">
            Вы смотрите как гость: лента, калькулятор и избранное доступны.
            AI-анализ цены, сохранённые машины и уведомления в Telegram —
            после входа.
          </p>
          <Link to="/login" className="btn btn--primary btn--block profile-card__cta">
            Войти / Регистрация
          </Link>
        </section>
      )}

      <section className="panel profile-card">
        <h3>Регион доставки</h3>
        <div className="region-row">
          {(["ru", "by"] as const).map((r) => (
            <button
              key={r}
              className={region === r ? "chip chip--active" : "chip"}
              onClick={() => {
                haptic("light");
                setRegion(r);
                setRegionState(r);
              }}
            >
              {REGION_LABEL[r]} · {REGION_CURRENCY[r]}
            </button>
          ))}
        </div>
        <div className="region-hint">
          В ленте цены — в валюте региона; $ и цена источника остаются в карточке
          объявления. Таможенные платежи ЕАЭС одинаковы для РФ и РБ; нюансы
          постановки на учёт уточнит менеджер.
        </div>
      </section>

      <section className="panel profile-card">
        <h3>Профиль</h3>
        <div className="info-row">
          <span className="info-row__label">Имя</span>
          <span className="info-row__value">
            {user.first_name} {user.last_name ?? ""}
            {user.username ? <span className="info-row__muted"> · @{user.username}</span> : null}
          </span>
          <span className="badge badge--claimed">{TIER_LABEL[user.subscription_tier]}</span>
        </div>
        <div className="info-row">
          <span className="info-row__label">Роль</span>
          <span className="info-row__value">{user.role}</span>
        </div>
        <div className="info-row">
          <span className="info-row__label">С нами с</span>
          <span className="info-row__value">{new Date(user.created_at).toLocaleDateString("ru")}</span>
        </div>
      </section>

      <section className="panel profile-card">
        <h3>Подписка</h3>
        <div className="sub-tier">
          <div className="sub-tier__name">{TIER_LABEL[sub?.tier ?? user.subscription_tier]}</div>
          <div className="sub-tier__until">{until ? `активна до ${until}` : "бесплатный тариф"}</div>
        </div>
        {sub && (
          <div className="sub-limits">
            {sub.max_filters ?? "∞"} уведомлений · {sub.ai_daily_limit ?? "∞"} AI-сообщений/день
            · instant: {sub.instant ? "да" : "нет"}
          </div>
        )}
        <div className="sub-actions">
          {sub?.tier !== "pro" && (
            <button className="btn btn--primary" disabled={buying === "pro_month"} onClick={() => buy("pro", "month")}>
              {buying === "pro_month" ? "Оформляем…" : "⭐ Pro — 500★/мес"}
            </button>
          )}
          {sub?.tier !== "pro" && (
            <button className="btn" disabled={buying === "pro_year"} onClick={() => buy("pro", "year")}>
              {buying === "pro_year" ? "Оформляем…" : "⭐ Pro — 4800★/год (−20%)"}
            </button>
          )}
          {sub?.tier !== "business" && (
            <button className="btn" disabled={buying === "business_month"} onClick={() => buy("business", "month")}>
              {buying === "business_month" ? "Оформляем…" : "⭐ Business — 2500★/мес"}
            </button>
          )}
        </div>
        {message && (
          <div className={
            message.startsWith("✅") || message.startsWith("⏳")
              ? "profile-msg profile-msg--ok"
              : "profile-msg profile-msg--err"
          }>
            {message}
          </div>
        )}
      </section>

      <button className="btn btn--danger btn--block" onClick={logout}>
        Выйти (сбросить сессию)
      </button>

      {tg && (
        <div className="info-row info-row--muted">
          <span className="info-row__label">Контекст Mini App активен</span>
          <span className="info-row__value">Telegram ID: {tg.id}</span>
        </div>
      )}
    </div>
  );
}
