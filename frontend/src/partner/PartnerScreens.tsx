/** Экраны партнёрского ЛК (UI/UX ТЗ §4.3–4.6). */

import { useCallback, useEffect, useState } from "react";

import {
  PartnerApiError,
  papi,
  type BatchClaimOut,
  type BillingOut,
  type PartnerLead,
  type PartnerMe,
} from "./api";
import { useToast } from "../ui/Toast";
import { EmptyState, ErrorState } from "../ui/States";

/** Квалификация человеко-читаемо (до score — [бэкенд]). */
function qualText(q: Record<string, unknown> | null): string {
  if (!q) return "—";
  const parts: string[] = [];
  if (q.budget_usd) parts.push(`бюджет ${Number(q.budget_usd).toLocaleString("ru")} $`);
  if (q.timeframe) parts.push(`срок: ${String(q.timeframe)}`);
  if (q.prepay !== undefined) parts.push(q.prepay ? "предоплата: да" : "предоплата: нет");
  return parts.join(" · ") || "—";
}

// --- Дашборд (ТЗ §4.3): всё из GET /api/partner/me ---

export function Dashboard({ me }: { me: PartnerMe }) {
  return (
    <>
      <h1 className="pa-page-title">{me.name}</h1>
      <div className="kpi-grid">
        <div className="kpi">
          <div className="kpi__value">
            {me.claimed_today} / {me.daily_quota}
          </div>
          <div className="kpi__label">Квота на сегодня</div>
        </div>
        <div className="kpi">
          <div className="kpi__value">{me.quota_remaining}</div>
          <div className="kpi__label">Доступно к claim</div>
        </div>
        <div className="kpi">
          <div className="kpi__value">{me.plan}</div>
          <div className="kpi__label">Тариф</div>
        </div>
      </div>
      <div className="panel">
        <h3>Как это работает</h3>
        <p style={{ color: "var(--text-secondary)", fontSize: 14, margin: 0 }}>
          Забирайте лиды из пула эксклюзивно — контакты открываются сразу после claim.
          Пожалуйста, отмечайте качество лида (принят / возврат / брак / сделка):
          это влияет на доступ к пулу.
        </p>
      </div>
    </>
  );
}

// --- Пул лидов (ТЗ §4.4): claim в 1 клик, 409/429 состояния ---

export function Pool({
  onUnauthorized,
  onClaimed,
}: {
  onUnauthorized: () => void;
  onClaimed: () => void;
}) {
  const toast = useToast();
  const [leads, setLeads] = useState<PartnerLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [claiming, setClaiming] = useState<number | null>(null);
  const [claimingBatch, setClaimingBatch] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    papi<PartnerLead[]>("/leads?limit=50")
      .then(setLeads)
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError((e as Error).message);
      })
      .finally(() => setLoading(false));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  const claim = async (leadId: number) => {
    setClaiming(leadId);
    try {
      await papi(`/leads/${leadId}/claim`, { method: "POST" });
      toast.show("✅ Лид захвачен — контакты в «Моих лидах»");
      onClaimed();
      setLeads((prev) => prev.filter((l) => l.id !== leadId));
    } catch (e) {
      if (e instanceof PartnerApiError) {
        if (e.status === 401) {
          onUnauthorized();
          return;
        }
        if (e.status === 409) {
          toast.show("Этот лид уже забрал другой партнёр", { error: true });
          load();
        } else if (e.status === 429) {
          toast.show("Дневная квота исчерпана — попробуйте завтра", { error: true });
        } else {
          toast.show(e.message, { error: true });
        }
      }
    } finally {
      setClaiming(null);
    }
  };

  const claimBatch = async (limit: number) => {
    setClaimingBatch(true);
    try {
      const res = await papi<BatchClaimOut>("/leads/batch", {
        method: "POST",
        body: JSON.stringify({ limit }),
      });
      if (res.claimed.length === 0) {
        toast.show("Нет доступных лидов", { error: true });
      } else {
        toast.show(`✅ Забрано лидов: ${res.claimed.length} — контакты в «Моих лидах»`);
      }
      onClaimed();
      const taken = new Set(res.claimed.map((c) => c.lead_id));
      setLeads((prev) => prev.filter((l) => !taken.has(l.id)));
    } catch (e) {
      if (e instanceof PartnerApiError) {
        if (e.status === 401) {
          onUnauthorized();
          return;
        }
        if (e.status === 429) {
          toast.show("Дневная квота исчерпана — попробуйте завтра", { error: true });
        } else {
          toast.show(e.message, { error: true });
        }
      }
    } finally {
      setClaimingBatch(false);
    }
  };

  return (
    <>
      <h1 className="pa-page-title">Пул лидов</h1>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>
          {leads.length} доступно
        </span>
        <button
          className="btn btn--primary"
          disabled={loading || !!error || claimingBatch || leads.length === 0}
          onClick={() => void claimBatch(3)}
        >
          {claimingBatch ? "Забираю…" : "⚡ Взять 3"}
        </button>
      </div>
      {loading ? (
        <div className="panel">Загрузка…</div>
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : leads.length === 0 ? (
        <EmptyState
          title="Нет доступных лидов"
          description="Проверьте позже — новые появляются постоянно"
        />
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th>
              <th>Квалификация</th>
              <th>Комментарий</th>
              <th>Источник</th>
              <th>Создан</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {leads.map((l) => (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>{qualText(l.qualification)}</td>
                <td>{l.comment ?? "—"}</td>
                <td>{l.source ?? "—"}</td>
                <td>{new Date(l.created_at).toLocaleString("ru")}</td>
                <td>
                  <button
                    className="btn btn--primary"
                    disabled={claiming === l.id}
                    onClick={() => void claim(l.id)}
                  >
                    {claiming === l.id ? "…" : "Claim"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

// --- Мои лиды + обязательный фидбек (ТЗ §4.5) ---

const FEEDBACK: { key: string; label: string; danger?: boolean }[] = [
  { key: "accepted", label: "Принят" },
  { key: "returned", label: "Возврат", danger: true },
  { key: "junk", label: "Брак", danger: true },
  { key: "deal", label: "Сделка" },
];

export function MyLeads({ onUnauthorized }: { onUnauthorized: () => void }) {
  const toast = useToast();
  const [leads, setLeads] = useState<PartnerLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    papi<PartnerLead[]>("/leads?mine=true&limit=100")
      .then(setLeads)
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError((e as Error).message);
      })
      .finally(() => setLoading(false));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  const sendFeedback = async (leadId: number, status: string) => {
    try {
      await papi(`/leads/${leadId}/feedback`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      toast.show("✅ Отметка сохранена");
      setLeads((prev) =>
        prev.map((l) => (l.id === leadId ? { ...l, partner_status: status } : l)),
      );
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) {
        onUnauthorized();
        return;
      }
      toast.show("Не удалось сохранить отметку", { error: true });
    }
  };

  return (
    <>
      <h1 className="pa-page-title">Мои лиды</h1>
      {loading ? (
        <div className="panel">Загрузка…</div>
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : leads.length === 0 ? (
        <EmptyState title="Пока пусто" description="Заберите первые лиды из пула" />
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th>
              <th>Клиент</th>
              <th>Квалификация</th>
              <th>Захвачен</th>
              <th>Статус</th>
              <th>Фидбек</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((l) => (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>
                  <div className="lead-contact">
                    Telegram ID: <b>{l.user_id ?? "—"}</b>
                    {l.comment && <div>💬 {l.comment}</div>}
                  </div>
                </td>
                <td>{qualText(l.qualification)}</td>
                <td>{l.claimed_at ? new Date(l.claimed_at).toLocaleString("ru") : "—"}</td>
                <td>
                  <span
                    className={l.partner_status ? `badge badge--${l.partner_status}` : "badge"}
                  >
                    {l.partner_status ?? "claimed"}
                  </span>
                </td>
                <td>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {FEEDBACK.filter((f) => f.key !== l.partner_status).map((f) => (
                      <button
                        key={f.key}
                        className={f.danger ? "btn btn--danger" : "btn"}
                        style={{ padding: "4px 10px" }}
                        onClick={() => void sendFeedback(l.id, f.key)}
                      >
                        {f.label}
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

// --- API-ключ (ТЗ §4.6): префикс + мини-доки; ротация через менеджера ---

export function ApiKeyScreen({ me }: { me: PartnerMe }) {
  const toast = useToast();
  const prefix = me.key_prefix ?? "pi_****";
  return (
    <>
      <h1 className="pa-page-title">API-ключ</h1>
      <div className="panel">
        <h3>Текущий ключ</h3>
        <p style={{ fontSize: 16 }}>
          <code>{prefix}…</code>
        </p>
        <p style={{ color: "var(--text-secondary)", fontSize: 14 }}>
          Полное значение ключа показывается только один раз при выдаче. Ротация — через
          вашего менеджера: старый ключ перестанет работать немедленно.
        </p>
      </div>
      <div className="panel">
        <h3>API (кратко)</h3>
        <pre
          style={{
            background: "var(--bg-secondary)",
            padding: 12,
            borderRadius: 10,
            overflowX: "auto",
            fontSize: 13,
          }}
        >
{`GET  /api/partner/me                     — состояние и квота
GET  /api/partner/leads                  — пул доступных
GET  /api/partner/leads?mine=true        — мои лиды
POST /api/partner/leads/{id}/claim       — захват лида
POST /api/partner/leads/batch            — захват до N лидов {"limit":3}
GET  /api/partner/billing                — план, квота, тарифы
POST /api/partner/leads/{id}/feedback    — фидбек (accepted/returned/junk/deal)`}
        </pre>
        <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          Заголовок: <code>X-API-Key: pi_…</code>
        </p>
      </div>
      <button
        className="btn"
        onClick={() => {
          void navigator.clipboard.writeText(prefix);
          toast.show("Префикс скопирован");
        }}
      >
        Скопировать префикс
      </button>
    </>
  );
}

// --- Биллинг (ТЗ §4.2): текущий план, расход квоты, каталог тарифов ---

export function Billing({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [billing, setBilling] = useState<BillingOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    papi<BillingOut>("/billing")
      .then(setBilling)
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError((e as Error).message);
      });
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <ErrorState description={error} onRetry={load} />;
  if (!billing) return <div className="panel">Загрузка…</div>;

  const usedPct =
    billing.daily_quota > 0
      ? Math.min(100, Math.round((billing.claimed_today / billing.daily_quota) * 100))
      : 0;

  return (
    <>
      <h1 className="pa-page-title">Биллинг</h1>
      <div className="panel">
        <h3>Текущий план: {billing.plan}</h3>
        <p style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 4 }}>
          Оплата за лид (CPL): списание за каждый захваченный лид. Смена тарифа —
          через вашего менеджера.
        </p>
        <div style={{ marginTop: 12 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 14,
              marginBottom: 6,
            }}
          >
            <span>
              Сегодня: <b>{billing.claimed_today}</b> / {billing.daily_quota}
            </span>
            <span style={{ color: "var(--text-secondary)" }}>
              доступно {billing.quota_remaining}
            </span>
          </div>
          <div
            style={{
              height: 8,
              borderRadius: 4,
              background: "var(--bg-secondary)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${usedPct}%`,
                height: "100%",
                background: "var(--accent)",
              }}
            />
          </div>
        </div>
      </div>
      <div className="panel">
        <h3>Тарифы</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>Тариф</th>
              <th>Квота в день</th>
              <th>Цена/мес</th>
            </tr>
          </thead>
          <tbody>
            {billing.plans.map((p) => (
              <tr
                key={p.key}
                style={p.key === billing.plan ? { fontWeight: 600 } : undefined}
              >
                <td>
                  {p.title}
                  {p.key === billing.plan && (
                    <span className="badge" style={{ marginLeft: 8 }}>
                      текущий
                    </span>
                  )}
                </td>
                <td>{p.daily_quota} лидов</td>
                <td>{p.price_usd} $</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}


