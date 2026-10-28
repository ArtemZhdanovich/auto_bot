/**
 * Партнёрский UI-кит (04 §6.2): компактные компоненты плотного
 * интерфейса данных — KpiTile, StatusPill, QuotaMeter, Chip, Skeleton,
 * Sheet, EmptyBlock/ErrorBlock, CodeBlock + форматтеры (§7).
 *
 * Стили — partner.css (токены из index.css переиспользуются через var(--…)).
 * Иконки-клише (машинки) не используем (§6.3): марка пустого состояния —
 * геометрическая рамка, как в 02 §7.9.
 */

import { useEffect, useRef, type ReactNode } from "react";

// ---------- Форматтеры (§7, P6, R4) ----------

/** Относительное время (P6): «5 мин назад» · «сегодня 12:40» · «вчера». */
export function fmtRelTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const diff = now - t;
  if (diff < 0) return "только что";
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const d = new Date(t);
  const today = new Date(now);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const sameDay = d.toDateString() === today.toDateString();
  if (sameDay) return `сегодня ${hm}`;
  const yesterday = new Date(now - 86_400_000);
  if (d.toDateString() === yesterday.toDateString()) return `вчера ${hm}`;
  return d.toLocaleDateString("ru");
}

/** Свежий лид (<30 мин) — бейдж «Новый» (04 §4.2). */
export function isFreshLead(iso: string | null | undefined, now = Date.now()): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return Number.isFinite(t) && now - t < 30 * 60_000;
}

/** Локализованное число с обычными пробелами (NBSP из ru-RU нормализуем). */
function numRu(v: number): string {
  return Math.round(v)
    .toLocaleString("ru-RU")
    .replace(/\u00A0/g, " ");
}

/** Полные деньги: «18 400 000 ₩» / «240 $» (null → «—»). */
export function fmtMoney(v: number | null | undefined, currency?: string | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${numRu(v)}${currency ? ` ${currency}` : ""}`;
}

/** Компактные деньги (R4): «18,4 млн ₩» / «920 тыс.» / «$21 400». */
export function fmtMoneyCompact(v: number | null | undefined, currency?: string | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const cur = currency ? ` ${currency}` : "";
  if (abs >= 1_000_000) {
    const m = v / 1_000_000;
    const str = m >= 100 ? String(Math.round(m)) : m.toFixed(1).replace(".", ",");
    return `${str} млн${cur}`;
  }
  if (abs >= 100_000) {
    // сотни тысяч — компактно; десятки тысяч читаются целиком (точность важнее)
    const k = v / 1_000;
    return `${numRu(k)} тыс.${cur}`;
  }
  return `${numRu(v)}${cur}`;
}

/** Доллары CPL/баланса: «$12», «$12.5», «$240». */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const rounded = Math.abs(v - Math.round(v)) < 0.005 ? Math.round(v) : v.toFixed(2).replace(/0$/, "");
  return `$${rounded}`;
}

/** Части квалификации для чипов (бюджет крупнее — тикер-цифры). */
export interface QualParts {
  budget: number | null;
  timeframe: string | null;
  prepay: boolean | null;
}

export function qualParts(q: Record<string, unknown> | null | undefined): QualParts {
  if (!q) return { budget: null, timeframe: null, prepay: null };
  const budget = Number(q.budget_usd);
  return {
    budget: Number.isFinite(budget) && budget > 0 ? budget : null,
    timeframe: q.timeframe != null ? String(q.timeframe) : null,
    prepay: typeof q.prepay === "boolean" ? q.prepay : null,
  };
}

/** Строка квалификации (для поиска/подсказок). */
export function qualText(q: Record<string, unknown> | null | undefined): string {
  const p = qualParts(q);
  const parts: string[] = [];
  if (p.budget) parts.push(`бюджет ${fmtMoney(p.budget, "$")}`);
  if (p.timeframe) parts.push(`срок: ${p.timeframe}`);
  if (p.prepay !== null) parts.push(p.prepay ? "предоплата: да" : "предоплата: нет");
  return parts.join(" · ");
}

/** Deep-link на клиента в Telegram (M1). */
export function tgUserLink(userId: number): string {
  return `tg://user?id=${userId}`;
}

/** Склонение «лид/лида/лидов» для подписей «хватит на N …». */
export function leadsPlural(n: number): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return "лидов";
  if (b > 1 && b < 5) return "лида";
  if (b === 1) return "лид";
  return "лидов";
}

/** Заготовка менеджеру о пополнении (§7.2). */
export function topupTemplate(name: string, affordable: number): string {
  return `Здравствуйте! Счёт «${name}»: просьба пополнить баланс. Сейчас хватит на ${affordable} ${leadsPlural(affordable)}.`;
}

/** Заготовка менеджеру о ротации ключа (§7.7). */
export function rotationTemplate(name: string, prefix: string): string {
  return `Здравствуйте! Просьба ротировать ключ партнёра «${name}» (префикс ${prefix}).`;
}

/** Копирование с фолбэком на execCommand (jsdom/старые WebView). */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* фолбэк ниже */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

// ---------- Skeleton (X3, 02 §7.7) ----------

/** Скелетон-блок фиксированной геометрии (без layout-shift при подмене). */
export function Skel({ w, h = 14, r = 6 }: { w: string | number; h?: number; r?: number }) {
  return <span className="pa-skel" style={{ width: w, height: h, borderRadius: r }} aria-hidden="true" />;
}

/** Скелетон-строки списка/таблицы. */
export function SkelRows({ n = 5 }: { n?: number }) {
  return (
    <div className="pa-skel-rows" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="pa-skel-row">
          <Skel w={48} h={16} />
          <Skel w="46%" h={14} />
          <Skel w={72} h={14} />
        </div>
      ))}
    </div>
  );
}

/** Скелетон KPI-плиток (той же сетки, что данные). */
export function SkelKpis({ n = 4 }: { n?: number }) {
  return (
    <div className="pa-kpi-grid" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="pa-kpi">
          <Skel w="55%" h={12} />
          <Skel w="40%" h={26} />
          <Skel w="70%" h={12} />
        </div>
      ))}
    </div>
  );
}

// ---------- Пустые состояния и ошибки (§7, 02 §7.9) ----------

/** Геометрическая марка (не машинка-клише, 04 §6.3): вложенные рамки. */
function Mark({ danger }: { danger?: boolean }) {
  return (
    <svg
      className={danger ? "pa-mark pa-mark--danger" : "pa-mark"}
      width="64"
      height="64"
      viewBox="0 0 64 64"
      fill="none"
      aria-hidden="true"
    >
      <rect x="6" y="6" width="52" height="52" rx="14" stroke="currentColor" strokeWidth="2" />
      <rect x="18" y="18" width="28" height="28" rx="8" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

export function EmptyBlock({
  title,
  description,
  actionLabel,
  onAction,
}: {
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="pa-empty">
      <Mark />
      <div className="pa-empty__title">{title}</div>
      {description && <div className="pa-empty__desc">{description}</div>}
      {actionLabel && onAction && (
        <button type="button" className="btn pa-empty__action" onClick={onAction}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}

export function ErrorBlock({
  title = "Что-то пошло не так",
  description,
  actionLabel = "Повторить",
  onAction,
}: {
  title?: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="pa-empty">
      <Mark danger />
      <div className="pa-empty__title">{title}</div>
      {description && <div className="pa-empty__desc">{description}</div>}
      {actionLabel && onAction && (
        <button type="button" className="btn pa-empty__action" onClick={onAction}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}

// ---------- KpiTile (04 §6.2) ----------

export type KpiTone = "normal" | "warn" | "danger" | "accent";

export function KpiTile({
  label,
  value,
  hint,
  tone = "normal",
  loading,
  onClick,
  ariaLabel,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: KpiTone;
  loading?: boolean;
  onClick?: () => void;
  ariaLabel?: string;
}) {
  const cls = [
    "pa-kpi",
    tone !== "normal" ? `pa-kpi--${tone}` : "",
    onClick ? "pa-kpi--action" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const body = (
    <>
      <div className="pa-kpi__label">{label}</div>
      {loading ? (
        <Skel w="46%" h={26} />
      ) : (
        <div className="pa-kpi__value">{value}</div>
      )}
      {hint && <div className="pa-kpi__hint">{hint}</div>}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} aria-label={ariaLabel}>
        {body}
      </button>
    );
  }
  return <div className={cls}>{body}</div>;
}

// ---------- Chip (02 §7.2) ----------

export function Chip({
  label,
  active,
  count,
  onClick,
  onRemove,
  title,
}: {
  label: string;
  active?: boolean;
  count?: number;
  onClick?: () => void;
  onRemove?: () => void;
  title?: string;
}) {
  const chip = (
    <button
      type="button"
      className={active ? "pa-chip pa-chip--active" : "pa-chip"}
      onClick={onClick}
      aria-pressed={onClick ? active : undefined}
      title={title}
    >
      {label}
      {count !== undefined && count > 0 && <span className="pa-chip__count">{count}</span>}
    </button>
  );
  // Крест удаления — отдельная кнопка рядом (не вложенная): интерактив
  // внутри интерактива невалиден для a11y (02 §7.2 + ТЗ-аудит 03).
  if (!onRemove) return chip;
  return (
    <span className="pa-chip-wrap">
      {chip}
      <button
        type="button"
        className="pa-chip__x"
        aria-label={`Удалить запрос «${label}»`}
        onClick={onRemove}
      >
        ×
      </button>
    </span>
  );
}

// ---------- StatusPill (04 §6.2) ----------

const STATUS_META: Record<string, { label: string; cls: string }> = {
  claimed: { label: "Новый", cls: "pa-pill--new" },
  accepted: { label: "Принят", cls: "pa-pill--accepted" },
  deal: { label: "Сделка", cls: "pa-pill--deal" },
  returned: { label: "Возврат", cls: "pa-pill--returned" },
  junk: { label: "Брак", cls: "pa-pill--junk" },
};

/** Цвет + слово, не только цвет (дот-индикатор, 02 §7.4). */
export function StatusPill({ status }: { status: string | null | undefined }) {
  const meta = status ? STATUS_META[status] : undefined;
  if (!meta) {
    return (
      <span className="pa-pill">
        <span className="pa-pill__dot" />
        {status ?? "—"}
      </span>
    );
  }
  return (
    <span className={`pa-pill ${meta.cls}`}>
      <span className="pa-pill__dot" />
      {meta.label}
    </span>
  );
}

// ---------- QuotaMeter (04 §6.2, B3) ----------

export function QuotaMeter({
  claimed,
  quota,
  remaining,
  loading,
  compact,
}: {
  claimed: number;
  quota: number;
  remaining: number;
  loading?: boolean;
  /** Только полоса, без подписей — для KPI-плитки Обзора (04 §4.1). */
  compact?: boolean;
}) {
  if (loading) {
    return <Skel w="100%" h={compact ? 6 : 44} r={compact ? 3 : 10} />;
  }
  const usedPct = quota > 0 ? Math.min(100, Math.round((claimed / quota) * 100)) : 0;
  const done = remaining <= 0;
  const almost = usedPct >= 80;
  return (
    <div className="pa-quota" role="group" aria-label="Квота на сегодня">
      {!compact && (
        <div className="pa-quota__labels">
          <span className="pa-quota__count">
            {claimed} из {quota}
          </span>
          <span className="pa-quota__rest">
            {done ? "обновится завтра" : `осталось ${remaining}`}
          </span>
        </div>
      )}
      <div
        className="pa-quota__track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={quota}
        aria-valuenow={claimed}
        aria-label="Использовано квоты за сегодня"
      >
        <div
          className={`pa-quota__fill${done ? " pa-quota__fill--done" : almost ? " pa-quota__fill--warn" : ""}`}
          style={{ width: `${usedPct}%` }}
        />
      </div>
      {!compact && almost && (
        <div className="pa-quota__note">
          {done ? "Квота исчерпана — обновится завтра в 00:00" : "Квота почти исчерпана"}
        </div>
      )}
    </div>
  );
}

// ---------- TabBadge (04 §6.2) ----------

export function TabBadge({ count, dot }: { count?: number; dot?: boolean }) {
  if (dot) return <span className="pa-navbadge pa-navbadge--dot" aria-hidden="true" />;
  if (!count || count <= 0) return null;
  return (
    <span className="pa-navbadge" aria-label={`${count} новых`}>
      {count > 9 ? "9+" : count}
    </span>
  );
}

// ---------- Sheet (02 §7.10): подтверждения/ротация ----------

export function Sheet({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="pa-sheet-wrap" role="presentation" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={panelRef}
        className="pa-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="pa-sheet__handle" aria-hidden="true" />
        <div className="pa-sheet__head">
          <div className="pa-sheet__title">{title}</div>
          <button type="button" className="pa-sheet__close" aria-label="Закрыть" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="pa-sheet__body">{children}</div>
        {footer && <div className="pa-sheet__footer">{footer}</div>}
      </div>
    </div>
  );
}

// ---------- CodeBlock (04 §6.2, K1) ----------

export function CodeBlock({ code }: { code: string }) {
  return (
    <pre className="pa-code" aria-hidden={false}>
      <code>{code}</code>
    </pre>
  );
}
