/** Экраны админ-панели (UI/UX ТЗ §3.4–3.6). */

import { useCallback, useEffect, useState } from "react";

import {
  AdminApiError,
  aapi,
  type AdminLead,
  type AdminPartner,
  type PartnerKeyOut,
  type SourceConfig,
  type SourceHealth,
} from "./api";
import { useToast } from "../ui/Toast";
import { EmptyState, ErrorState } from "../ui/States";

// --- Источники (ТЗ §3.4) ---

export function Sources() {
  const toast = useToast();
  const [sources, setSources] = useState<SourceConfig[]>([]);
  const [health, setHealth] = useState<Record<string, SourceHealth>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    aapi<SourceConfig[]>("/sources")
      .then(async (srcs) => {
        setSources(srcs);
        const h: Record<string, SourceHealth> = {};
        await Promise.all(
          srcs.map(async (s) => {
            try {
              h[s.name] = await aapi<SourceHealth>(`/sources/${s.name}/health`);
            } catch {
              /* нет данных — ок */
            }
          }),
        );
        setHealth(h);
      })
      .catch((e) => {
        if (e instanceof AdminApiError && e.status === 401) return window.location.reload();
        setError((e as Error).message);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (s: SourceConfig) => {
    try {
      await aapi(`/sources/${s.name}`, {
        method: "PUT",
        body: JSON.stringify({ ...s, enabled: !s.enabled }),
      });
      toast.show(`✅ Источник ${s.name} ${s.enabled ? "выключен" : "включён"}`);
      load();
    } catch (e) {
      toast.show((e as Error).message, { error: true });
    }
  };

  const fetchNow = async (name: string) => {
    toast.show(`⏳ Запускаю fetch «${name}»…`);
    try {
      await aapi(`/sources/${name}/fetch`, { method: "POST" });
      toast.show("✅ Fetch завершён");
    } catch (e) {
      toast.show((e as Error).message, { error: true });
    }
  };

  return (
    <>
      <h1 className="pa-page-title">Источники</h1>
      {loading ? (
        <div className="panel">Загрузка…</div>
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>Имя</th>
              <th>Адаптер</th>
              <th>Тип</th>
              <th>Cron</th>
              <th>Ошибки подряд</th>
              <th>Всего fetched</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => {
              const h = health[s.name];
              return (
                <tr key={s.name}>
                  <td>
                    <b>{s.name}</b>
                  </td>
                  <td>{s.adapter_class}</td>
                  <td>{s.kind}</td>
                  <td>{s.schedule_cron ?? "—"}</td>
                  <td style={h && h.error_streak > 0 ? { color: "var(--destructive)" } : undefined}>
                    {h ? h.error_streak : "—"}
                  </td>
                  <td>{h ? h.total_fetched : "—"}</td>
                  <td>
                    <span className={s.enabled ? "badge badge--accepted" : "badge"}>
                      {s.enabled ? "включён" : "выключен"}
                    </span>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="btn" onClick={() => void toggle(s)}>
                        {s.enabled ? "Выключить" : "Включить"}
                      </button>
                      <button className="btn btn--primary" onClick={() => void fetchNow(s.name)}>
                        Fetch now
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

// --- Партнёры (ТЗ §3.5): создание, ключ-один-раз, ротация ---

function KeyOnceModal({ title, rawKey, onClose }: { title: string; rawKey: string; onClose: () => void }) {
  const toast = useToast();
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div className="panel" style={{ width: "min(520px, 92vw)" }}>
        <h3>{title}</h3>
        <p style={{ color: "var(--destructive)", fontSize: 14 }}>
          Ключ показывается <b>один раз</b>. Скопируйте его сейчас.
        </p>
        <pre
          style={{
            background: "var(--bg-secondary)",
            padding: 12,
            borderRadius: 10,
            overflowX: "auto",
            userSelect: "all",
          }}
        >
          {rawKey}
        </pre>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn btn--primary"
            onClick={() => {
              void navigator.clipboard.writeText(rawKey);
              toast.show("Ключ скопирован");
            }}
          >
            Скопировать
          </button>
          <button className="btn" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}

export function Partners({
  partners,
  onUnauthorized,
  onChanged,
}: {
  partners: AdminPartner[];
  onUnauthorized: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [plan, setPlan] = useState("starter");
  const [quota, setQuota] = useState(10);
  const [keyOnce, setKeyOnce] = useState<{ title: string; key: string } | null>(null);

  const err = (e: unknown) => {
    if (e instanceof AdminApiError && e.status === 401) {
      onUnauthorized();
      return;
    }
    toast.show((e as Error).message, { error: true });
  };

  const create = async () => {
    try {
      const out = await aapi<PartnerKeyOut>("/partners", {
        method: "POST",
        body: JSON.stringify({ name, contact: contact || null, plan, daily_quota: quota }),
      });
      setShowCreate(false);
      setName("");
      setContact("");
      setKeyOnce({ title: `Ключ партнёра «${name}»`, key: out.key });
      onChanged();
    } catch (e) {
      err(e);
    }
  };

  const rotate = async (p: AdminPartner) => {
    if (!window.confirm(`Ротировать ключ «${p.name}»? Старый ключ перестанет работать немедленно.`)) {
      return;
    }
    try {
      const out = await aapi<PartnerKeyOut>(`/partners/${p.id}/rotate`, { method: "POST" });
      setKeyOnce({ title: `Новый ключ «${p.name}»`, key: out.key });
    } catch (e) {
      err(e);
    }
  };

  const toggleStatus = async (p: AdminPartner) => {
    const status = p.status === "active" ? "suspended" : "active";
    try {
      await aapi(`/partners/${p.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      toast.show(`✅ Партнёр ${p.name}: ${status}`);
      onChanged();
    } catch (e) {
      err(e);
    }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="pa-page-title">Партнёры</h1>
        <button className="btn btn--primary" onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? "Отмена" : "+ Создать"}
        </button>
      </div>

      {showCreate && (
        <div className="panel">
          <h3>Новый партнёр</h3>
          <div style={{ display: "grid", gridTemplateColumns: "2fr 2fr 1fr 1fr", gap: 8 }}>
            <input className="input" placeholder="Название" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="Контакт" value={contact} onChange={(e) => setContact(e.target.value)} />
            <select className="input" value={plan} onChange={(e) => setPlan(e.target.value)}>
              <option value="starter">starter</option>
              <option value="pro">pro</option>
              <option value="salon">salon</option>
            </select>
            <input
              className="input"
              type="number"
              min={1}
              max={1000}
              value={quota}
              onChange={(e) => setQuota(Number(e.target.value))}
            />
          </div>
          <button className="btn btn--primary" style={{ marginTop: 8 }} disabled={!name} onClick={() => void create()}>
            Создать и выдать ключ
          </button>
        </div>
      )}

      {partners.length === 0 ? (
        <EmptyState title="Партнёров пока нет" />
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>Название</th>
              <th>План</th>
              <th>Квота (сегодня)</th>
              <th>Ключ</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {partners.map((p) => (
              <tr key={p.id}>
                <td>{p.id}</td>
                <td>
                  <b>{p.name}</b>
                  {p.contact && (
                    <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>{p.contact}</div>
                  )}
                </td>
                <td>{p.plan}</td>
                <td>
                  {p.claimed_today} / {p.daily_quota}
                </td>
                <td>
                  <code style={{ fontSize: 12 }}>{p.key_prefix ?? "—"}</code>
                </td>
                <td>
                  <span className={p.status === "active" ? "badge badge--accepted" : "badge badge--returned"}>
                    {p.status}
                  </span>
                </td>
                <td>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn" onClick={() => void rotate(p)}>
                      Ротировать ключ
                    </button>
                    <button className="btn" onClick={() => void toggleStatus(p)}>
                      {p.status === "active" ? "Приостановить" : "Активировать"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {keyOnce && (
        <KeyOnceModal title={keyOnce.title} rawKey={keyOnce.key} onClose={() => setKeyOnce(null)} />
      )}
    </>
  );
}

// --- Лиды (ТЗ §3.6, read-only) ---

export function AdminLeads({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [leads, setLeads] = useState<AdminLead[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const q = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
    aapi<AdminLead[]>(`/leads${q}`)
      .then(setLeads)
      .catch((e) => {
        if (e instanceof AdminApiError && e.status === 401) onUnauthorized();
        else setError((e as Error).message);
      })
      .finally(() => setLoading(false));
  }, [statusFilter, onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="pa-page-title">Лиды</h1>
        <select
          className="input"
          style={{ width: 200 }}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">Все статусы</option>
          <option value="new">new</option>
          <option value="in_progress">in_progress</option>
          <option value="closed">closed</option>
          <option value="rejected">rejected</option>
        </select>
      </div>

      {loading ? (
        <div className="panel">Загрузка…</div>
      ) : error ? (
        <ErrorState description={error} onRetry={load} />
      ) : leads.length === 0 ? (
        <EmptyState title="Лидов нет" description="Измените фильтр или дождитесь новых заявок" />
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>Клиент</th>
              <th>Статус</th>
              <th>Партнёр</th>
              <th>Фидбек</th>
              <th>Квалификация</th>
              <th>Создан</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((l) => (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>{l.user_id}</td>
                <td>
                  <span className="badge">{l.status}</span>
                </td>
                <td>{l.partner_id ?? "—"}</td>
                <td>
                  {l.partner_status ? (
                    <span className={`badge badge--${l.partner_status}`}>{l.partner_status}</span>
                  ) : (
                    "—"
                  )}
                </td>
                <td style={{ maxWidth: 260 }}>{JSON.stringify(l.qualification) ?? "—"}</td>
                <td>{new Date(l.created_at).toLocaleString("ru")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p style={{ color: "var(--text-secondary)", fontSize: 12 }}>
        Действия с лидами (экспорт CSV, детали) — на следующих этапах.
      </p>
    </>
  );
}



