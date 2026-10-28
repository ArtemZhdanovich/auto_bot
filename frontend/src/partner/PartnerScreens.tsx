/**
 * Экраны партнёрского ЛК — редизайн по docs/REDESIGN/04-partner-redesign.md §4:
 * Обзор, Пул лидов, Мои лиды, Рынок, Биллинг, Ключ API.
 * Редизайн 05-chats-marketplace (F2): + Чаты (seller/deal-треды счёта) и
 * Витрина (мои лоты на внутреннем рынке).
 * Копирайт — готовые строки §7; ошибки API — только через маппинг §7.9 (X2);
 * списки проходят skeleton → empty → error → data (X3).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  PartnerApiError,
  addPartnerOffer,
  addPartnerLot,
  batchClaimCap,
  buyMarketPackage,
  fetchMarketPackages,
  fetchPartnerChats,
  fetchPartnerLots,
  fetchPartnerOffers,
  leadsAffordable,
  papi,
  partnerFail,
  planTitle,
  removePartnerOffer,
  removePartnerLot,
  toBillingOut,
  toMarketOut,
  type BatchClaimOut,
  type BillingOut,
  type MarketLot,
  type MarketOut,
  type PartnerLead,
  type PartnerMe,
  type PartnerOffer,
  type PartnerThread,
  type PartnerTransaction,
} from "./api";
import type { MarketPackage, MyLot } from "../api/types";
import { digits } from "../lib/fx";
import { ChatList } from "../components/chat/ChatList";
import { PartnerChatRoom } from "./components/chat/PartnerChatRoom";
import {
  Chip,
  CodeBlock,
  EmptyBlock,
  ErrorBlock,
  KpiTile,
  QuotaMeter,
  Sheet,
  SkelKpis,
  SkelRows,
  StatusPill,
  copyToClipboard,
  fmtMoney,
  fmtMoneyCompact,
  fmtRelTime,
  fmtUsd,
  isFreshLead,
  leadsPlural,
  qualParts,
  qualText,
  rotationTemplate,
  tgUserLink,
  topupTemplate,
} from "./components/bits";
import { usePToast } from "./components/toast";

// ---------- Общие хелперы экранов ----------

/** Мобильный режим (<768px): карточки вместо таблиц (P1/M5/R5). */
function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() => {
    try {
      return window.matchMedia?.("(max-width: 767px)").matches ?? false;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    const mq = window.matchMedia?.("(max-width: 767px)");
    if (!mq || typeof mq.addEventListener !== "function") return;
    const onChange = (e: MediaQueryListEvent) => setMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

export interface ScreenProps {
  me: PartnerMe;
  /** 401 — ключ отозван/сменён: возврат на экран входа с баннером (X4). */
  onUnauthorized: () => void;
  /** Данные менялись (claim/фидбек) — партнёр /me и бейджи обновятся в App. */
  onChanged: () => void;
}

/** Ошибка загрузки секции: 401 — наверх, иначе русский текст §7.9. */
function sectionError(
  e: unknown,
  onUnauthorized: () => void,
  setErr: (s: string) => void,
): void {
  if (e instanceof PartnerApiError && e.status === 401) {
    onUnauthorized();
    return;
  }
  setErr(partnerFail(e).text);
}

/** Ошибка действия (claim/batch): тост с кнопкой по виду ошибки (§7.9). */
function actionError(
  e: unknown,
  toast: ReturnType<typeof usePToast>,
  nav: ReturnType<typeof useNavigate>,
  onUnauthorized: () => void,
  refresh?: () => void,
): void {
  const fail = partnerFail(e);
  if (fail.kind === "auth") {
    onUnauthorized();
    return;
  }
  const action =
    fail.kind === "balance"
      ? { label: "К биллингу", onClick: () => nav("/partner/billing") }
      : fail.kind === "quota"
        ? { label: "К обзору", onClick: () => nav("/partner") }
        : fail.kind === "taken" || fail.kind === "network"
          ? refresh && { label: "Обновить", onClick: refresh }
          : refresh && { label: "Повторить", onClick: refresh };
  toast.show(fail.text, { error: true, action: action ?? undefined });
}

/** Кнопка «Забрать» дизейблится заранее с причиной (P4): null = можно. */
function claimBlockReason(me: PartnerMe): string | null {
  if (me.quota_remaining <= 0) return "Квота на сегодня исчерпана — обновится завтра";
  if (me.cpl_cost > me.balance) return "Недостаточно баланса — пополните счёт у менеджера";
  return null;
}

/** Batch-claim «Взять до N» (В2/P5): N = min(квота, баланс/CPL, 10). */
function useBatchClaim({ me, onUnauthorized, onChanged }: ScreenProps) {
  const toast = usePToast();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async () => {
    const cap = batchClaimCap(me.quota_remaining, me.balance, me.cpl_cost);
    if (cap <= 0 || busy) return;
    setBusy(true);
    try {
      const res = await papi<BatchClaimOut>("/leads/batch", {
        method: "POST",
        body: JSON.stringify({ limit: cap }),
      });
      if (res.claimed.length === 0) {
        toast.show("Свободных лидов нет — проверьте позже");
      } else {
        toast.show(`✅ Забрано лидов: ${res.claimed.length}`, {
          action: { label: "Открыть", onClick: () => nav("/partner/leads") },
        });
      }
      onChanged();
    } catch (e) {
      actionError(e, toast, nav, onUnauthorized);
    } finally {
      setBusy(false);
    }
  }, [me.quota_remaining, me.balance, me.cpl_cost, busy, toast, nav, onUnauthorized, onChanged]);
  return { run, busy };
}

/** Чипы квалификации лида (бюджет — крупнее, тикер-цифры, 04 §4.2). */
function QualChips({ lead }: { lead: PartnerLead }) {
  const p = qualParts(lead.qualification);
  return (
    <div className="pa-qual">
      {p.budget != null && (
        <span className="pa-qual__budget" title="Бюджет клиента">
          {fmtMoney(p.budget, "$")}
        </span>
      )}
      {p.timeframe && <span className="pa-qual__chip">{p.timeframe}</span>}
      {p.prepay !== null && (
        <span className="pa-qual__chip">{p.prepay ? "предоплата" : "без предоплаты"}</span>
      )}
      {p.budget == null && !p.timeframe && p.prepay === null && (
        <span className="pa-qual__chip">без квалификации</span>
      )}
    </div>
  );
}

// ============================================================
// Обзор (04 §4.1, бывший «Дашборд»)
// ============================================================

const LOW_BALANCE_DISMISS = "autobot.partner.lowBalanceDismissed";

export function Overview(props: ScreenProps) {
  const { me, onUnauthorized } = props;
  const toast = usePToast();
  const nav = useNavigate();
  const batch = useBatchClaim(props);

  const [pool, setPool] = useState<PartnerLead[] | null>(null);
  const [poolErr, setPoolErr] = useState<string | null>(null);
  const [mine, setMine] = useState<PartnerLead[] | null>(null);
  const [mineErr, setMineErr] = useState<string | null>(null);
  const [market, setMarket] = useState<MarketOut | null>(null);
  const [marketErr, setMarketErr] = useState<string | null>(null);
  const [bannerHidden, setBannerHidden] = useState(
    () => sessionStorage.getItem(LOW_BALANCE_DISMISS) === "1",
  );

  const load = useCallback(() => {
    papi<PartnerLead[]>("/leads?limit=50")
      .then(setPool)
      .catch((e) => sectionError(e, onUnauthorized, setPoolErr));
    papi<PartnerLead[]>("/leads?mine=true&limit=100")
      .then(setMine)
      .catch((e) => sectionError(e, onUnauthorized, setMineErr));
    papi<unknown>("/market?limit=10")
      .then((raw) => setMarket(toMarketOut(raw)))
      .catch((e) => sectionError(e, onUnauthorized, setMarketErr));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  const affordable = leadsAffordable(me.balance, me.cpl_cost);
  const cap = batchClaimCap(me.quota_remaining, me.balance, me.cpl_cost);
  const freshCount = useMemo(() => {
    if (!pool) return null;
    const hourAgo = Date.now() - 3_600_000;
    return pool.filter((l) => Date.parse(l.created_at) > hourAgo).length;
  }, [pool]);
  const pending = useMemo(
    () =>
      mine
        ? [...mine]
            .filter((l) => (l.partner_status ?? "claimed") === "claimed")
            .sort((a, b) => Date.parse(b.claimed_at ?? "") - Date.parse(a.claimed_at ?? ""))
        : null,
    [mine],
  );
  const topDeals = useMemo(
    () =>
      market
        ? [...market.items]
            .filter((l) => l.discount_pct != null)
            .sort((a, b) => (b.discount_pct ?? 0) - (a.discount_pct ?? 0))
            .slice(0, 3)
        : null,
    [market],
  );

  const balanceTone = affordable < 1 ? "danger" : affordable < 3 ? "warn" : "normal";
  const capHint =
    me.quota_remaining <= 0
      ? "Квота исчерпана — обновится завтра"
      : affordable < 1
        ? "Не хватает баланса на следующий лид"
        : null;

  return (
    <>
      <h1 className="pa-title">Обзор</h1>

      {/* Предупреждение о низком балансе (§4.1.2): только когда есть проблема */}
      {!bannerHidden && balanceTone !== "normal" && (
        <div className={balanceTone === "danger" ? "pa-banner pa-banner--danger" : "pa-banner"}>
          <span>
            {affordable < 1
              ? "Баланса не хватит на следующий лид. Новые лиды не удастся забрать до пополнения."
              : `Баланса хватит на ${affordable} ${leadsPlural(affordable)}. Напишите менеджеру о пополнении.`}
          </span>
          <div className="pa-banner__actions">
            <button
              type="button"
              className="btn"
              onClick={async () => {
                const ok = await copyToClipboard(topupTemplate(me.name, affordable));
                toast.show(ok ? "✅ Заготовка скопирована" : "Не удалось скопировать", {
                  error: !ok,
                });
              }}
            >
              Написать менеджеру
            </button>
            <button
              type="button"
              className="pa-banner__close"
              aria-label="Скрыть предупреждение"
              onClick={() => {
                sessionStorage.setItem(LOW_BALANCE_DISMISS, "1");
                setBannerHidden(true);
              }}
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* KPI-строка (§4.1.3) */}
      {poolErr ? (
        <ErrorBlock description={poolErr} onAction={load} />
      ) : (
        <div className="pa-kpi-grid">
          <KpiTile
            label="Баланс"
            value={fmtUsd(me.balance)}
            hint={`хватит на ${affordable} ${leadsPlural(affordable)}`}
            tone={balanceTone}
          />
          <div className="pa-kpi">
            <div className="pa-kpi__label">Квота на сегодня</div>
            <div className="pa-kpi__value">
              {me.claimed_today} из {me.daily_quota}
            </div>
            <div className="pa-kpi__hint">осталось {me.quota_remaining} · обновится завтра</div>
            <QuotaMeter
              claimed={me.claimed_today}
              quota={me.daily_quota}
              remaining={me.quota_remaining}
              compact
            />
          </div>
          <KpiTile
            label="Новые лиды в пуле"
            value={freshCount ?? "—"}
            hint="за последний час · нажмите, чтобы открыть пул"
            tone={freshCount != null && freshCount > 0 ? "accent" : "normal"}
            onClick={() => nav("/partner/pool")}
            ariaLabel={`Новые лиды в пуле: ${freshCount ?? 0}. Перейти к пулу`}
          />
          {marketErr ? (
            <div className="pa-kpi">
              <div className="pa-kpi__label">Выгодные лоты</div>
              <div className="pa-kpi__value">—</div>
              <div className="pa-kpi__hint">рынок недоступен</div>
            </div>
          ) : (
            <KpiTile
              label="Выгодные лоты"
              value={market ? market.items.length : "—"}
              hint={
                market && market.median != null
                  ? `медиана ${fmtMoneyCompact(market.median, market.items[0]?.currency ?? null)}`
                  : "дешевле медианы"
              }
            />
          )}
        </div>
      )}

      {/* Быстрые действия (§4.1.4) — самые крупные кнопки экрана */}
      <div className="pa-quick">
        <button
          type="button"
          className="btn btn--primary pa-quick__btn"
          disabled={cap === 0 || batch.busy}
          title={capHint ?? undefined}
          onClick={() => void batch.run()}
        >
          {batch.busy ? "Забираем…" : cap > 0 ? `Взять до ${cap} ${leadsPlural(cap)}` : "Взять лиды"}
        </button>
        <button
          type="button"
          className="btn pa-quick__btn"
          onClick={() => nav("/partner/pool")}
        >
          Перейти к пулу
        </button>
      </div>
      {cap === 0 && capHint && <p className="pa-quick__hint">{capHint}</p>}

      {/* Ждут отметки (§4.1.5) */}
      <section className="pa-section">
        <div className="pa-section__head">
          <h2 className="pa-section__title">
            Ждут отметки{pending && pending.length > 0 ? ` · ${pending.length}` : ""}
          </h2>
          {pending && pending.length > 0 && (
            <button type="button" className="pa-link" onClick={() => nav("/partner/leads")}>
              Открыть все →
            </button>
          )}
        </div>
        {mineErr ? (
          <ErrorBlock description={mineErr} onAction={load} />
        ) : mine === null ? (
          <SkelRows n={3} />
        ) : pending === null || pending.length === 0 ? (
          <p className="pa-section__empty">Все ваши лиды отмечены — так держать.</p>
        ) : (
          <div className="pa-pending">
            {pending.slice(0, 5).map((l) => (
              <button
                type="button"
                key={l.id}
                className="pa-pending__row"
                onClick={() => nav("/partner/leads")}
              >
                <span className="pa-pending__id">#{l.id}</span>
                <span className="pa-pending__qual">{qualText(l.qualification) || "—"}</span>
                <span className="pa-pending__time">{fmtRelTime(l.claimed_at)}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* Выгодные лоты дня (§4.1.6) */}
      <section className="pa-section">
        <div className="pa-section__head">
          <h2 className="pa-section__title">Выгодные лоты сегодня</h2>
          <button type="button" className="pa-link" onClick={() => nav("/partner/market")}>
            Весь рынок →
          </button>
        </div>
        {marketErr ? (
          <ErrorBlock description={marketErr} onAction={load} />
        ) : market === null ? (
          <SkelRows n={3} />
        ) : topDeals === null || topDeals.length === 0 ? (
          <p className="pa-section__empty">
            Лотов дешевле медианы сейчас нет — возвращайтесь после обновления базы.
          </p>
        ) : (
          <div className="pa-deals">
            {topDeals.map((lot) => (
              <LotRow key={lot.id ?? `${lot.make}-${lot.model}-${lot.year}`} lot={lot} />
            ))}
          </div>
        )}
      </section>

      {/* Обучение — свёрнуто (§4.1.7) */}
      <details className="pa-how">
        <summary>Как работает пул лидов</summary>
        <p>
          Забирайте лиды из пула эксклюзивно — контакты открываются сразу после захвата.
          Отмечайте качество лида (принят / возврат / брак / сделка): это влияет на доступ
          к пулу. Цена каждого захваченного лида списывается с баланса.
        </p>
      </details>
    </>
  );
}

/** Компактный ряд выгодного лота (Обзор и Рынок): машина · цена · −N% · действия. */
function LotRow({ lot }: { lot: MarketLot }) {
  const name = `${lot.make ?? ""} ${lot.model ?? ""}`.trim() || "—";
  return (
    <div className="pa-deal">
      <div className="pa-deal__main">
        <span className="pa-deal__name">
          {name}
          {lot.year != null && <span className="pa-deal__year"> · {lot.year}</span>}
        </span>
        <span className="pa-deal__price" title={fmtMoney(lot.price, lot.currency)}>
          {fmtMoneyCompact(lot.price, lot.currency)}
        </span>
        {lot.discount_pct != null && lot.discount_pct !== 0 && (
          <span className="pa-deal__badge">−{Math.abs(Math.round(lot.discount_pct))}%</span>
        )}
      </div>
      <div className="pa-deal__actions">
        {lot.id != null && (
          <a
            className="btn"
            href={`/listing/${lot.id}`}
            target="_blank"
            rel="noreferrer noopener"
          >
            Карточка
          </a>
        )}
        {lot.url && (
          <a className="btn" href={lot.url} target="_blank" rel="noreferrer noopener">
            Источник ↗
          </a>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Пул лидов (04 §4.2)
// ============================================================

type BudgetFilter = "low" | "mid" | "high" | null;

const BUDGET_CHIPS: { key: Exclude<BudgetFilter, null>; label: string; test: (b: number) => boolean }[] = [
  { key: "low", label: "до $10 тыс.", test: (b) => b < 10_000 },
  { key: "mid", label: "$10–20 тыс.", test: (b) => b >= 10_000 && b < 20_000 },
  { key: "high", label: "$20 тыс.+", test: (b) => b >= 20_000 },
];

export function Pool(props: ScreenProps) {
  const { me, onUnauthorized, onChanged } = props;
  const toast = usePToast();
  const nav = useNavigate();
  const isMobile = useIsMobile();
  const batch = useBatchClaim(props);

  const [leads, setLeads] = useState<PartnerLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [budget, setBudget] = useState<BudgetFilter>(null);
  const [freshOnly, setFreshOnly] = useState(false);
  const [claiming, setClaiming] = useState<number | null>(null);
  const [fading, setFading] = useState<ReadonlySet<number>>(new Set());

  const load = useCallback(
    (offset = 0) => {
      const append = offset > 0;
      const setBusy = append ? setLoadingMore : setLoading;
      const setErr = append ? () => undefined : setError;
      setBusy(true);
      if (!append) setError(null);
      papi<PartnerLead[]>(`/leads?limit=50&offset=${offset}`)
        .then((data) => {
          setExhausted(data.length < 50);
          setLeads((prev) => {
            if (!append) return data;
            const seen = new Set(prev.map((l) => l.id));
            return [...prev, ...data.filter((l) => !seen.has(l.id))];
          });
        })
        .catch((e) => {
          if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
          else if (!append) setErr(partnerFail(e).text);
          else toast.show(partnerFail(e).text, { error: true });
        })
        .finally(() => setBusy(false));
    },
    [onUnauthorized, toast],
  );

  useEffect(() => {
    load(0);
  }, [load]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return leads
      .filter((l) => {
        if (needle) {
          const hay = `${l.comment ?? ""} ${qualText(l.qualification)}`.toLowerCase();
          if (!hay.includes(needle)) return false;
        }
        if (budget) {
          const b = qualParts(l.qualification).budget;
          if (b == null || !BUDGET_CHIPS.find((c) => c.key === budget)!.test(b)) return false;
        }
        if (freshOnly && !isFreshLead(l.created_at)) return false;
        return true;
      })
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)); // новые сверху
  }, [leads, q, budget, freshOnly]);

  const hasFilters = Boolean(q.trim() || budget || freshOnly);
  const blocked = claimBlockReason(me);
  const cap = batchClaimCap(me.quota_remaining, me.balance, me.cpl_cost);

  const claim = async (leadId: number) => {
    if (blocked || claiming != null) return;
    setClaiming(leadId);
    try {
      await papi(`/leads/${leadId}/claim`, { method: "POST" });
      toast.show("✅ Лид забран — контакты в «Моих лидах»", {
        action: { label: "Открыть", onClick: () => nav("/partner/leads") },
      });
      onChanged();
      // fade-out ≤300ms (02 §6.2), потом убираем из списка
      setFading((prev) => new Set(prev).add(leadId));
      window.setTimeout(() => {
        setLeads((prev) => prev.filter((l) => l.id !== leadId));
        setFading((prev) => {
          const next = new Set(prev);
          next.delete(leadId);
          return next;
        });
      }, 280);
    } catch (e) {
      actionError(e, toast, nav, onUnauthorized, () => load(0));
    } finally {
      setClaiming(null);
    }
  };

  const resetFilters = () => {
    setQ("");
    setBudget(null);
    setFreshOnly(false);
  };

  return (
    <>
      <h1 className="pa-title">Пул лидов</h1>

      {/* Строка-фильтр (§4.2.1, P2): поиск + чипы бюджета + «только новые» */}
      <div className="pa-filters">
        <input
          className="input pa-filters__q"
          type="search"
          placeholder="Поиск по комментариям: «Kia», «срочно»…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Поиск по лидам"
        />
        <div className="pa-filters__chips" role="group" aria-label="Фильтры по бюджету">
          {BUDGET_CHIPS.map((c) => (
            <Chip
              key={c.key}
              label={c.label}
              active={budget === c.key}
              onClick={() => setBudget(budget === c.key ? null : c.key)}
            />
          ))}
          <Chip label="Только новые" active={freshOnly} onClick={() => setFreshOnly(!freshOnly)} />
        </div>
      </div>

      {/* Тулбар (§4.2.2): счётчик + batch «Взять до N» */}
      <div className="pa-toolbar">
        <span className="pa-toolbar__count">
          {loading ? "Загружаем пул…" : `Показано ${filtered.length} · новые сверху`}
        </span>
        <button
          type="button"
          className="btn btn--primary"
          disabled={loading || !!error || batch.busy || cap === 0 || leads.length === 0}
          title={
            cap === 0
              ? me.quota_remaining <= 0
                ? "Квота на сегодня исчерпана"
                : "Недостаточно баланса"
              : undefined
          }
          onClick={() => void batch.run()}
        >
          {batch.busy ? "Забираем…" : cap > 0 ? `Взять до ${cap}` : "Взять лиды"}
        </button>
      </div>

      {loading ? (
        <SkelRows n={5} />
      ) : error ? (
        <ErrorBlock description={error} onAction={() => load(0)} />
      ) : leads.length === 0 ? (
        <EmptyBlock
          title="Свободных лидов сейчас нет"
          description="Новые появятся в течение дня: бейдж на вкладке «Пул» вас предупредит."
          actionLabel="Обновить"
          onAction={() => load(0)}
        />
      ) : hasFilters && filtered.length === 0 ? (
        <EmptyBlock
          title="Под фильтры ничего не подошло"
          description="Сбросьте чипы или расширьте бюджет."
          actionLabel="Сбросить фильтры"
          onAction={resetFilters}
        />
      ) : isMobile ? (
        <div className="pa-cards">
          {filtered.map((l) => (
            <article key={l.id} className={fading.has(l.id) ? "pa-leadcard pa-leadcard--gone" : "pa-leadcard"}>
              <div className="pa-leadcard__head">
                <span className="pa-leadcard__id">#{l.id}</span>
                {isFreshLead(l.created_at) && <span className="pa-badge-new">Новый</span>}
                <span className="pa-leadcard__time">{fmtRelTime(l.created_at)}</span>
              </div>
              <QualChips lead={l} />
              {l.comment && <p className="pa-leadcard__comment">{l.comment}</p>}
              <div className="pa-leadcard__foot">
                <button
                  type="button"
                  className="btn btn--primary pa-leadcard__claim"
                  disabled={!!blocked || claiming === l.id}
                  onClick={() => void claim(l.id)}
                >
                  {claiming === l.id ? "Забираем…" : `Забрать · ${fmtUsd(me.cpl_cost)}`}
                </button>
                {blocked && claiming !== l.id && <p className="pa-leadcard__blocked">{blocked}</p>}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <table className="tbl pa-pool-table">
          <thead>
            <tr>
              <th>№</th>
              <th>Квалификация</th>
              <th>Комментарий</th>
              <th>Источник</th>
              <th>Создан</th>
              <th aria-label="Действие" />
            </tr>
          </thead>
          <tbody>
            {filtered.map((l) => (
              <tr key={l.id} className={fading.has(l.id) ? "pa-row--gone" : undefined}>
                <td className="pa-num">#{l.id}</td>
                <td>
                  <QualChips lead={l} />
                </td>
                <td className="pa-pool-table__comment">{l.comment ?? "—"}</td>
                <td>{l.source ?? "—"}</td>
                <td>
                  <div className="pa-created">
                    {isFreshLead(l.created_at) && <span className="pa-badge-new">Новый</span>}
                    <span>{fmtRelTime(l.created_at)}</span>
                  </div>
                </td>
                <td className="pa-num">
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={!!blocked || claiming === l.id}
                    title={blocked ?? undefined}
                    onClick={() => void claim(l.id)}
                  >
                    {claiming === l.id ? "Забираем…" : `Забрать · ${fmtUsd(me.cpl_cost)}`}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Подгрузка (P3): +50 по offset */}
      {!loading && !error && !exhausted && leads.length > 0 && (
        <div className="pa-more">
          <button
            type="button"
            className="btn"
            disabled={loadingMore}
            onClick={() => load(leads.length)}
          >
            {loadingMore ? "Загружаем…" : "Показать ещё"}
          </button>
        </div>
      )}
    </>
  );
}

// ============================================================
// Мои лиды (04 §4.3): статусная лента
// ============================================================

type MyFilter = "pending" | "work" | "closed";

const FEEDBACK_ACTIONS: { key: string; label: string; primary?: boolean; danger?: boolean }[] = [
  { key: "deal", label: "Сделка", primary: true },
  { key: "accepted", label: "Принят" },
  { key: "returned", label: "Возврат", danger: true },
  { key: "junk", label: "Брак", danger: true },
];

const FEEDBACK_LABELS: Record<string, string> = {
  deal: "сделка",
  accepted: "принят",
  returned: "возврат",
  junk: "брак",
};

function leadBucket(l: PartnerLead): MyFilter {
  const s = l.partner_status ?? "claimed";
  if (s === "claimed") return "pending";
  if (s === "accepted") return "work";
  return "closed";
}

export function MyLeads({ me, onUnauthorized, onChanged }: ScreenProps) {
  const toast = usePToast();
  const nav = useNavigate();
  const isMobile = useIsMobile();
  const [leads, setLeads] = useState<PartnerLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<MyFilter>("pending");
  /** Деструктивный фидбек (Возврат/Брак) — подтверждение (§4.3.2). */
  const [confirming, setConfirming] = useState<{ leadId: number; status: string; label: string } | null>(null);
  const [sending, setSending] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    papi<PartnerLead[]>("/leads?mine=true&limit=100")
      .then(setLeads)
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError(partnerFail(e).text);
      })
      .finally(() => setLoading(false));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  const counts = useMemo(
    () => ({
      pending: leads.filter((l) => leadBucket(l) === "pending").length,
      work: leads.filter((l) => leadBucket(l) === "work").length,
      closed: leads.filter((l) => leadBucket(l) === "closed").length,
    }),
    [leads],
  );

  const visible = useMemo(
    () =>
      leads
        .filter((l) => leadBucket(l) === filter)
        // новые захваченные сверху; без фидбека — выше (§4.3.3)
        .sort((a, b) => {
          const ap = leadBucket(a) === "pending" ? 0 : 1;
          const bp = leadBucket(b) === "pending" ? 0 : 1;
          return ap - bp || Date.parse(b.claimed_at ?? "") - Date.parse(a.claimed_at ?? "");
        }),
    [leads, filter],
  );

  /** Фидбек: optimistic-статус с откатом (§4.3). */
  const sendFeedback = async (leadId: number, status: string) => {
    const prevStatus = leads.find((l) => l.id === leadId)?.partner_status ?? "claimed";
    setLeads((prev) => prev.map((l) => (l.id === leadId ? { ...l, partner_status: status } : l)));
    setSending(leadId);
    try {
      await papi(`/leads/${leadId}/feedback`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      toast.show(`✅ Отметка сохранена: ${FEEDBACK_LABELS[status] ?? status}`);
      onChanged();
    } catch (e) {
      setLeads((prev) => prev.map((l) => (l.id === leadId ? { ...l, partner_status: prevStatus } : l)));
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show("Не удалось сохранить отметку — попробуйте ещё раз", { error: true });
    } finally {
      setSending(null);
    }
  };

  return (
    <>
      <h1 className="pa-title">Мои лиды</h1>

      {/* Чипы-фильтры = очередь работы (М2) */}
      <div className="pa-filters__chips" role="group" aria-label="Фильтр по статусам">
        <Chip
          label="Ждут отметки"
          count={counts.pending}
          active={filter === "pending"}
          onClick={() => setFilter("pending")}
        />
        <Chip label="В работе" count={counts.work} active={filter === "work"} onClick={() => setFilter("work")} />
        <Chip
          label="Закрытые"
          count={counts.closed}
          active={filter === "closed"}
          onClick={() => setFilter("closed")}
        />
      </div>

      {loading ? (
        <SkelRows n={4} />
      ) : error ? (
        <ErrorBlock description={error} onAction={load} />
      ) : leads.length === 0 ? (
        <EmptyBlock
          title="Вы ещё не забирали лиды"
          description="Заберите первые из пула — контакты откроются сразу."
          actionLabel="К пулу"
          onAction={() => nav("/partner/pool")}
        />
      ) : visible.length === 0 ? (
        <EmptyBlock title="Здесь пока пусто" description="В этом статусе лидов нет." />
      ) : (
        <div className={isMobile ? "pa-cards" : "pa-myleads"}>
          {visible.map((l) => (
            <article key={l.id} className="pa-mylead">
              <div className="pa-mylead__head">
                <span className="pa-mylead__id">#{l.id}</span>
                <StatusPill status={l.partner_status ?? "claimed"} />
                <span className="pa-mylead__time">захвачен {fmtRelTime(l.claimed_at)}</span>
              </div>
              <div className="pa-mylead__body">
                <div>
                  <QualChips lead={l} />
                  {l.comment && <p className="pa-mylead__comment">{l.comment}</p>}
                </div>
                {/* Контакт (M1): tg-deep-link + копирование ID одной кнопкой */}
                <div className="pa-mylead__contact">
                  <div className="pa-mylead__tg">
                    <span className="pa-mylead__tglabel">Telegram ID:</span>{" "}
                    {l.user_id != null ? (
                      <a className="pa-tglink" href={tgUserLink(l.user_id)}>
                        {l.user_id}
                      </a>
                    ) : (
                      "—"
                    )}
                  </div>
                  {l.user_id != null && (
                    <button
                      type="button"
                      className="btn pa-btn-sm"
                      onClick={async () => {
                        const ok = await copyToClipboard(String(l.user_id));
                        toast.show(ok ? "✅ ID скопирован" : "Не удалось скопировать", { error: !ok });
                      }}
                    >
                      Копировать ID
                    </button>
                  )}
                  <div className="pa-mylead__tghint">Найдите клиента по ID в поиске Telegram</div>
                </div>
              </div>
              {/* Фидбек в 1 клик; деструктивные — с подтверждением */}
              <div className="pa-mylead__feedback">
                {FEEDBACK_ACTIONS.filter((f) => f.key !== l.partner_status).map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className={
                      f.primary ? "btn btn--primary" : f.danger ? "btn btn--danger" : "btn"
                    }
                    disabled={sending === l.id}
                    onClick={() => {
                      if (f.danger) setConfirming({ leadId: l.id, status: f.key, label: f.label });
                      else void sendFeedback(l.id, f.key);
                    }}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}

      <Sheet
        open={confirming != null}
        title={confirming ? `Отметить «${confirming.label}»?` : ""}
        onClose={() => setConfirming(null)}
        footer={
          <>
            <button
              type="button"
              className="btn btn--danger"
              onClick={() => {
                if (confirming) void sendFeedback(confirming.leadId, confirming.status);
                setConfirming(null);
              }}
            >
              Отметить
            </button>
            <button type="button" className="btn" onClick={() => setConfirming(null)}>
              Отмена
            </button>
          </>
        }
      >
        <p className="pa-sheet__text">Отметка влияет на доступ к пулу.</p>
        <p className="pa-sheet__hint">Статус «{confirming?.label}» будет виден менеджеру.</p>
      </Sheet>

      {/* Подсказка тарифа не мешает: копирайт без «Claim»-жаргона (D4) */}
      <p className="pa-footnote">Цена захваченного лида: {fmtUsd(me.cpl_cost)} · тариф {planTitle(me.plan)}</p>
    </>
  );
}

// ============================================================
// Рынок (04 §4.4): сохранённые запросы + сводка + выгодные лоты
// ============================================================

const SAVED_QUERIES_KEY = "autobot.partner.marketQueries";

interface SavedQuery {
  make: string;
  model: string;
}

function loadSavedQueries(): SavedQuery[] {
  try {
    const raw = localStorage.getItem(SAVED_QUERIES_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (q): q is SavedQuery =>
          typeof q === "object" &&
          q != null &&
          typeof (q as SavedQuery).make === "string" &&
          typeof (q as SavedQuery).model === "string",
      )
      .slice(0, 6);
  } catch {
    return [];
  }
}

function persistSavedQueries(list: SavedQuery[]): void {
  try {
    localStorage.setItem(SAVED_QUERIES_KEY, JSON.stringify(list.slice(0, 6)));
  } catch {
    /* private mode */
  }
}

export function Market({ onUnauthorized }: { onUnauthorized: () => void }) {
  const isMobile = useIsMobile();
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [data, setData] = useState<MarketOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedQuery[]>(loadSavedQueries);
  /** Активный запрос — подсветка чипа и заголовок среза. */
  const [active, setActive] = useState<SavedQuery>({ make: "", model: "" });

  const load = useCallback(
    (m: string, mo: string) => {
      setLoading(true);
      setError(null);
      setActive({ make: m.trim(), model: mo.trim() });
      const params = new URLSearchParams();
      if (m.trim()) params.set("make", m.trim());
      if (mo.trim()) params.set("model", mo.trim());
      params.set("limit", "50");
      papi<unknown>(`/market?${params.toString()}`)
        .then((raw) => setData(toMarketOut(raw)))
        .catch((e) => {
          if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
          else setError(partnerFail(e).text);
        })
        .finally(() => setLoading(false));
    },
    [onUnauthorized],
  );

  useEffect(() => {
    load("", ""); // дефолт — весь рынок (§4.4.1)
  }, [load]);

  const search = (m: string, mo: string) => {
    load(m, mo);
    const has = m.trim() || mo.trim();
    if (!has) return;
    const next: SavedQuery[] = [
      { make: m.trim(), model: mo.trim() },
      ...saved.filter((s) => !(s.make === m.trim() && s.model === mo.trim())),
    ].slice(0, 6); // максимум 6, старые вытесняются
    setSaved(next);
    persistSavedQueries(next);
  };

  const removeSaved = (q: SavedQuery) => {
    const next = saved.filter((s) => !(s.make === q.make && s.model === q.model));
    setSaved(next);
    persistSavedQueries(next);
  };

  const deals = useMemo(
    () =>
      data
        ? [...data.items].sort((a, b) => (b.discount_pct ?? 0) - (a.discount_pct ?? 0))
        : null,
    [data],
  );
  const sliceTitle =
    [active.make, active.model].filter(Boolean).join(" ") || "Весь рынок";
  const makeOptions = Array.from(
    new Set(
      [
        ...saved.map((s) => s.make).filter(Boolean),
        ...(data?.items.map((i) => i.make).filter((m): m is string => Boolean(m)) ?? []),
      ].map((m) => m.trim()).filter(Boolean),
    ),
  );

  return (
    <>
      <h1 className="pa-title">Рынок</h1>

      {/* Сохранённые запросы (R1): чипы localStorage, максимум 6 */}
      {saved.length > 0 && (
        <div className="pa-saved" aria-label="Сохранённые запросы">
          {saved.map((q) => (
            <Chip
              key={`${q.make}/${q.model}`}
              label={[q.make, q.model].filter(Boolean).join(" ")}
              active={active.make === q.make && active.model === q.model}
              onClick={() => {
                setMake(q.make);
                setModel(q.model);
                load(q.make, q.model);
              }}
              onRemove={() => removeSaved(q)}
            />
          ))}
        </div>
      )}

      <form
        className="pa-market-form"
        onSubmit={(e) => {
          e.preventDefault();
          search(make, model);
        }}
      >
        <input
          className="input"
          list="pa-make-options"
          placeholder="Марка (Kia)"
          value={make}
          onChange={(e) => setMake(e.target.value)}
          aria-label="Марка"
        />
        <datalist id="pa-make-options">
          {makeOptions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <input
          className="input"
          placeholder="Модель (K5)"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          aria-label="Модель"
        />
        <button type="submit" className="btn btn--primary" disabled={loading}>
          {loading ? "Считаем…" : "Показать"}
        </button>
      </form>

      {loading ? (
        <>
          <SkelKpis n={4} />
          <SkelRows n={5} />
        </>
      ) : error ? (
        <ErrorBlock description={error} onAction={() => load(active.make, active.model)} />
      ) : data == null || data.count === 0 ? (
        <EmptyBlock
          title="По этому запросу объявлений нет"
          description="Проверьте написание марки и модели или посмотрите весь рынок."
          actionLabel="Сбросить"
          onAction={() => {
            setMake("");
            setModel("");
            load("", "");
          }}
        />
      ) : (
        <>
          {/* Сводка среза (§4.4.2) */}
          <h2 className="pa-slice">
            {sliceTitle} · {data.count.toLocaleString("ru-RU")} объявл.
          </h2>
          <div className="pa-kpi-grid">
            <KpiTile
              label="Медиана"
              value={fmtMoneyCompact(data.median, data.items[0]?.currency ?? null)}
              hint={data.median != null ? fmtMoney(data.median, data.items[0]?.currency ?? null) : undefined}
            />
            <KpiTile label="Выгодных лотов" value={data.items.length} hint="дешевле медианы" />
            <KpiTile
              label="Мин. цена"
              value={fmtMoneyCompact(data.min, data.items[0]?.currency ?? null)}
            />
            <KpiTile
              label="Макс. цена"
              value={fmtMoneyCompact(data.max, data.items[0]?.currency ?? null)}
            />
          </div>

          {/* Выгодные лоты (§4.4.3, R3): двойное действие */}
          {deals != null && deals.length === 0 ? (
            <EmptyBlock
              title="Лотов дешевле медианы сейчас нет"
              description="Возвращайтесь после обновления базы — медиана пересчитывается ежедневно."
            />
          ) : isMobile ? (
            <div className="pa-cards">
              {deals?.map((lot, i) => (
                <LotRow key={lot.id ?? i} lot={lot} />
              ))}
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Машина</th>
                  <th>Год</th>
                  <th>Цена</th>
                  <th>Выгода</th>
                  <th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {deals?.map((lot, i) => (
                  <tr key={lot.id ?? i}>
                    <td>{`${lot.make ?? ""} ${lot.model ?? ""}`.trim() || "—"}</td>
                    <td className="pa-num">{lot.year ?? "—"}</td>
                    <td className="pa-num" title={fmtMoney(lot.price, lot.currency)}>
                      {fmtMoneyCompact(lot.price, lot.currency)}
                    </td>
                    <td>
                      {lot.discount_pct != null && lot.discount_pct !== 0 ? (
                        <span className="pa-deal__badge">−{Math.abs(Math.round(lot.discount_pct))}%</span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <div className="pa-deal__actions">
                        {lot.id != null && (
                          <a
                            className="btn"
                            href={`/listing/${lot.id}`}
                            target="_blank"
                            rel="noreferrer noopener"
                          >
                            Карточка
                          </a>
                        )}
                        {lot.url && (
                          <a className="btn" href={lot.url} target="_blank" rel="noreferrer noopener">
                            Источник ↗
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}

// ============================================================
// Биллинг (04 §4.5, B1–B4)
// ============================================================

/** Тип транзакции → подпись (§7.6); суммы ±, списания — danger. */
function txKind(t: PartnerTransaction): { label: string; income: boolean } {
  if (t.amount > 0 || t.kind === "topup") return { label: "Пополнение", income: true };
  if (t.kind === "charge") return { label: "Списание за лид", income: false };
  return { label: "Корректировка", income: t.amount > 0 };
}

export function Billing({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [billing, setBilling] = useState<BillingOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    papi<unknown>("/billing")
      .then((raw) => setBilling(toBillingOut(raw)))
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError(partnerFail(e).text);
      })
      .finally(() => setLoading(false));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <ErrorBlock description={error} onAction={load} />;

  const affordable = billing ? leadsAffordable(billing.balance, billing.cpl_cost) : 0;
  const tone = affordable < 1 ? "danger" : affordable < 3 ? "warn" : "normal";

  return (
    <>
      <h1 className="pa-title">Биллинг</h1>
      {loading || !billing ? (
        <>
          <SkelKpis n={3} />
          <SkelRows n={4} />
        </>
      ) : (
        <>
          {/* Плитки (§4.5.1): деньги — главные */}
          <div className="pa-kpi-grid">
            <KpiTile label="Баланс" value={fmtUsd(billing.balance)} tone={tone} />
            <KpiTile label="Цена лида" value={fmtUsd(billing.cpl_cost)} />
            <KpiTile
              label="Хватит на"
              value={`${affordable} ${leadsPlural(affordable)}`}
              hint={affordable < 3 ? "пополните счёт у менеджера" : undefined}
              tone={tone}
            />
          </div>

          {tone !== "normal" && (
            <div className={tone === "danger" ? "pa-banner pa-banner--danger" : "pa-banner"}>
              {affordable < 1
                ? "Баланса не хватит на следующий лид. Новые лиды не удастся забрать до пополнения."
                : `Баланса хватит на ${affordable} ${leadsPlural(affordable)} — пополните счёт у менеджера.`}
            </div>
          )}

          {/* Квота (§4.5.2) */}
          <section className="pa-section">
            <h2 className="pa-section__title">Квота на сегодня</h2>
            <QuotaMeter
              claimed={billing.claimed_today}
              quota={billing.daily_quota}
              remaining={billing.quota_remaining}
            />
            <p className="pa-footnote">Обновится завтра в 00:00.</p>
          </section>

          <p className="pa-footnote">
            Оплата за лид: {fmtUsd(billing.cpl_cost)} списывается за каждый захваченный лид.
            Пополнение и смена тарифа — через менеджера.
          </p>

          {/* Транзакции (§4.5.3): секция рисуется только когда API начнёт отдавать */}
          {billing.transactions && billing.transactions.length > 0 && (
            <section className="pa-section">
              <h2 className="pa-section__title">Транзакции</h2>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Тип</th>
                    <th>Сумма</th>
                    <th>Комментарий</th>
                  </tr>
                </thead>
                <tbody>
                  {billing.transactions.map((t) => {
                    const kind = txKind(t);
                    return (
                      <tr key={String(t.id)}>
                        <td>{t.created_at ? fmtRelTime(t.created_at) : "—"}</td>
                        <td>{kind.label}</td>
                        <td className={`pa-num ${kind.income ? "pa-tx-income" : "pa-tx-outcome"}`}>
                          {kind.income ? "+" : "−"}
                          {fmtUsd(Math.abs(t.amount))}
                        </td>
                        <td className="pa-pool-table__comment">{t.comment ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}
          {billing.transactions && billing.transactions.length === 0 && (
            <section className="pa-section">
              <h2 className="pa-section__title">Транзакции</h2>
              <EmptyBlock
                title="Транзакций пока нет"
                description="Здесь появятся пополнения и списания за лиды."
              />
            </section>
          )}

          {/* Тарифы (§4.5.4, B4): CPL-колонки, а не абонент */}
          <section className="pa-section">
            <h2 className="pa-section__title">Тарифы</h2>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Тариф</th>
                  <th>Квота в день</th>
                  <th>Цена лида</th>
                  <th>Абонент</th>
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
                        <span className="pa-pill pa-pill--current" style={{ marginLeft: 8 }}>
                          текущий
                        </span>
                      )}
                      {p.description && (
                        <div className="pa-plan-desc">{p.description}</div>
                      )}
                    </td>
                    <td className="pa-num">{p.daily_quota}</td>
                    <td className="pa-num">{fmtUsd(p.cpl_cost)}</td>
                    <td className="pa-num">
                      {p.price_usd != null ? fmtUsd(p.price_usd) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </>
  );
}

// ============================================================
// Ключ API (04 §4.6, K1/K2)
// ============================================================

const API_DOCS = `GET  /api/partner/me                  — счёт, квота, баланс
GET  /api/partner/market?make=&model= — сводка рынка + выгодные лоты
GET  /api/partner/leads               — пул доступных лидов
GET  /api/partner/leads?mine=true     — мои лиды (с контактами)
POST /api/partner/leads/{id}/claim    — захват лида
POST /api/partner/leads/batch         — захват до N лидов {"limit":3}
POST /api/partner/leads/{id}/feedback — фидбек (accepted/returned/junk/deal)
GET  /api/partner/billing             — баланс, квота, тарифы`;

export function ApiKeyScreen({ me }: { me: PartnerMe }) {
  const toast = usePToast();
  const [rotateOpen, setRotateOpen] = useState(false);
  const prefix = me.key_prefix ?? "pi_****";
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const curlExample = `curl -s "${origin}/api/partner/me" -H "X-API-Key: pi_ВАШ_КЛЮЧ"`;

  return (
    <>
      <h1 className="pa-title">Ключ API</h1>

      <section className="pa-section">
        <div className="pa-keycard">
          <div className="pa-keycard__row">
            <span className="pa-keycard__label">Текущий ключ</span>
            <code className="pa-keycard__prefix">{prefix}…</code>
            <span className="pa-pill pa-pill--accepted">
              <span className="pa-pill__dot" />
              Активен
            </span>
          </div>
          <p className="pa-keycard__note">
            Полный ключ показывается один раз при выдаче менеджером. Вход — по нему же.
          </p>
        </div>
        <button type="button" className="btn" onClick={() => setRotateOpen(true)}>
          Запросить ротацию
        </button>
      </section>

      {/* Ротация (K2): подтверждение + заготовка менеджеру (self-service — §5.3-7) */}
      <Sheet
        open={rotateOpen}
        title="Запросить новый ключ?"
        onClose={() => setRotateOpen(false)}
        footer={
          <>
            <button
              type="button"
              className="btn btn--danger"
              onClick={async () => {
                const ok = await copyToClipboard(rotationTemplate(me.name, prefix));
                toast.show(ok ? "✅ Заготовка скопирована" : "Не удалось скопировать", {
                  error: !ok,
                });
                setRotateOpen(false);
              }}
            >
              Запросить у менеджера
            </button>
            <button type="button" className="btn" onClick={() => setRotateOpen(false)}>
              Отмена
            </button>
          </>
        }
      >
        <p className="pa-sheet__text">
          Текущий перестанет работать сразу — все вкладки с ним выйдут из кабинета.
        </p>
        <p className="pa-sheet__hint">
          Менеджер выдаст новый ключ — он показывается один раз, сохраните его сразу.
        </p>
      </Sheet>

      <section className="pa-section">
        <div className="pa-section__head">
          <h2 className="pa-section__title">Документация API</h2>
          <button
            type="button"
            className="btn"
            onClick={async () => {
              const ok = await copyToClipboard(curlExample);
              toast.show(ok ? "✅ Пример запроса скопирован" : "Не удалось скопировать", {
                error: !ok,
              });
            }}
          >
            Скопировать пример
          </button>
        </div>
        <CodeBlock code={`${API_DOCS}\n\n${curlExample}`} />
        <p className="pa-footnote">
          Заголовок авторизации: <code>X-API-Key: pi_…</code>
        </p>
      </section>
    </>
  );
}

// ============================================================
// Чаты (05-chats-marketplace.md §7, F2): seller/deal-треды счёта
// ============================================================

/** Overlay-роут /partner/chats/:id — чат поверх списка (по образцу мини-аппа). */
const PARTNER_CHAT_ROOM_RE = /^\/partner\/chats\/([^/]+)$/;

/**
 * Список тредов счёта (реюз ядра F1 ChatList: тип-бейдж, subject-миниатюра,
 * сниппет, время, unread) + оверлей-чат PartnerChatRoom.
 */
export function PartnerChats({ onUnauthorized }: { onUnauthorized: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = usePToast();
  const [threads, setThreads] = useState<PartnerThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** lead_id с летящим POST feedback — кнопка дизейблится (P4). */
  const [marking, setMarking] = useState<number | null>(null);

  const overlay = PARTNER_CHAT_ROOM_RE.exec(location.pathname);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchPartnerChats()
      .then((list) => setThreads(list ?? []))
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError(partnerFail(e).text);
      })
      .finally(() => setLoading(false));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  /**
   * Deal-треды своих лидов без финальной отметки «сделка» (долг ревизора
   * №6): CTA прямо в списке чатов — завершённую сделку отмечаем отсюда,
   * не уходя в «Мои лиды». Незаклейменные лиды (partner_id IS NULL) в
   * список не попадают — claim остаётся в пуле.
   */
  const dealPending = useMemo(
    () =>
      threads.filter(
        (t) => t.type === "deal" && t.lead_id != null && (t.lead_status ?? "claimed") !== "deal",
      ),
    [threads],
  );

  /** «Отметить сделкой»: подтверждение → POST feedback → тост + гасим CTA. */
  const markDeal = async (thread: PartnerThread) => {
    if (thread.lead_id == null || marking != null) return;
    if (!window.confirm("Сделка завершена?")) return;
    setMarking(thread.lead_id);
    try {
      await papi(`/leads/${thread.lead_id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ status: "deal" }),
      });
      // локально закрываем CTA (бейдж «ждут отметки» обновит poll ЛК)
      setThreads((prev) =>
        prev.map((t) => (t.id === thread.id ? { ...t, lead_status: "deal" } : t)),
      );
      toast.show("✅ Сделка отмечена — лид закрыт");
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show("Не удалось отметить сделку — попробуйте ещё раз", { error: true });
    } finally {
      setMarking(null);
    }
  };

  return (
    <div className="pa-chat-page">
      {/* Список: пока открыт чат — прячем классом, не размонтируем */}
      <div className={overlay ? "pa-chat-page__list keepalive-hidden" : "pa-chat-page__list"}>
        <h1 className="pa-title">Чаты</h1>
        <p className="pa-chat-page__hint">
          Покупатели пишут по лотам вашей витрины («Продавец») и по заявкам
          на агента («Сделка»).
        </p>
        {/* Сделки ждут отметки (№6): CTA над списком тредов */}
        {!loading && !error && dealPending.length > 0 && (
          <div className="pa-chat-deals" role="group" aria-label="Сделки ждут отметки">
            {dealPending.map((t) => (
              <div key={t.id} className="pa-chat-deal">
                <div className="pa-chat-deal__main">
                  <span className="pa-chat-deal__title">Сделка завершена?</span>
                  <span className="pa-chat-deal__hint">
                    {t.title || "Заявка на агента"} — отметьте лид как «Сделка».
                  </span>
                </div>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={marking === t.lead_id}
                  onClick={() => void markDeal(t)}
                >
                  {marking === t.lead_id ? "Сохраняем…" : "Отметить сделкой"}
                </button>
              </div>
            ))}
          </div>
        )}
        {loading ? (
          <SkelRows n={4} />
        ) : error ? (
          <ErrorBlock description={error} onAction={load} />
        ) : threads.length === 0 ? (
          <EmptyBlock
            title="Чатов пока нет"
            description="Поставьте лоты в витрину — покупатели увидят их во «Внутреннем рынке» и напишут вам как продавцу. Чат «Сделка» создаётся автоматически по заявке на агента."
          />
        ) : (
          <ChatList threads={threads} onSelect={(id) => navigate(`/partner/chats/${id}`)} />
        )}
      </div>

      {/* Overlay: PartnerChatRoom поверх списка */}
      {overlay && (
        <div className="pa-chat-page__overlay">
          <PartnerChatRoom
            threadId={overlay[1]}
            thread={threads.find((t) => t.id === overlay?.[1]) ?? null}
            onBack={() => navigate("/partner/chats")}
            onUnauthorized={onUnauthorized}
          />
        </div>
      )}
    </div>
  );
}

// ============================================================
// Витрина (05 §1/§7, F2): мои лоты на внутреннем рынке
// ============================================================

/**
 * Офферы счёта: карточка лота + note + «Убрать», форма добавления по id
 * (id виден в ссылке карточки /listing/N); 409 — лот уже в чужой витрине.
 */
export function Showcase({ onUnauthorized }: { onUnauthorized: () => void }) {
  const toast = usePToast();
  const [offers, setOffers] = useState<PartnerOffer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [listingId, setListingId] = useState("");
  const [note, setNote] = useState("");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchPartnerOffers()
      .then((list) => setOffers(list ?? []))
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setError(partnerFail(e).text);
      })
      .finally(() => setLoading(false));
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  /** Добавить лот: пре-валидация id>0 (P4-принцип), note — необязательна. */
  const add = async () => {
    const id = Number.parseInt(listingId.trim(), 10);
    if (!Number.isInteger(id) || id <= 0) {
      toast.show("id лота — целое число больше нуля", { error: true });
      return;
    }
    if (adding) return;
    setAdding(true);
    try {
      await addPartnerOffer(id, note.trim() || null);
      toast.show("✅ Лот в витрине — покупатели увидят его во «Внутреннем рынке»");
      setListingId("");
      setNote("");
      load();
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setAdding(false);
    }
  };

  /** Убрать лот: оптимистично, при ошибке — откат и тост (§7.9). */
  const remove = async (offerListingId: number) => {
    if (removing != null) return;
    const prev = offers;
    setRemoving(offerListingId);
    setOffers((cur) => cur.filter((o) => o.listing_id !== offerListingId));
    try {
      await removePartnerOffer(offerListingId);
      toast.show("Лот снят с витрины");
    } catch (e) {
      setOffers(prev);
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setRemoving(null);
    }
  };

  return (
    <>
      <h1 className="pa-title">Витрина</h1>
      <p className="pa-footnote">
        Лоты витрины видны покупателям в ленте «🏪 Внутренний рынок», а их
        вопросы приходят вам как продавцу — раздел «Чаты».
      </p>

      {loading ? (
        <SkelRows n={3} />
      ) : error ? (
        <ErrorBlock description={error} onAction={load} />
      ) : offers.length === 0 ? (
        <EmptyBlock
          title="Витрина пуста"
          description="Добавьте свои лоты: покупатели увидят их во «Внутреннем рынке» и смогут написать вам как продавцу."
        />
      ) : (
        <div className="pa-offers">
          {offers.map((o) => (
            <article key={o.listing_id} className="pa-offer">
              {o.image ? (
                <img className="pa-offer__img" src={o.image} alt="" loading="lazy" />
              ) : (
                <span className="pa-offer__img pa-offer__img--empty" aria-hidden="true">
                  🚗
                </span>
              )}
              <div className="pa-offer__main">
                <span className="pa-offer__name">
                  {[o.make, o.model, o.year].filter(Boolean).join(" ") || `Лот #${o.listing_id}`}
                </span>
                <span className="pa-offer__meta">
                  #{o.listing_id}
                  {o.price != null && ` · ${fmtMoneyCompact(o.price, o.currency)}`}
                  {o.created_at && ` · ${fmtRelTime(o.created_at)}`}
                </span>
                {o.note && <span className="pa-offer__note">{o.note}</span>}
              </div>
              <div className="pa-offer__actions">
                <a
                  className="btn pa-btn-sm"
                  href={`/listing/${o.listing_id}`}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  Карточка
                </a>
                <button
                  type="button"
                  className="btn btn--danger pa-btn-sm"
                  disabled={removing === o.listing_id}
                  onClick={() => void remove(o.listing_id)}
                >
                  {removing === o.listing_id ? "Убираем…" : "Убрать"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {/* Форма добавления: id лота + заметка */}
      <section className="pa-section">
        <h2 className="pa-section__title">Добавить лот</h2>
        <form
          className="pa-offer-form"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <input
            className="input"
            type="text"
            inputMode="numeric"
            placeholder="id лота"
            aria-label="id лота"
            value={listingId}
            onChange={(e) => setListingId(e.target.value)}
          />
          <input
            className="input"
            type="text"
            placeholder="Заметка для покупателя (необязательно)"
            aria-label="Заметка"
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button type="submit" className="btn btn--primary" disabled={adding}>
            {adding ? "Добавляем…" : "Добавить"}
          </button>
        </form>
        <p className="pa-footnote">id виден в ссылке карточки лота: /listing/N</p>
      </section>
    </>
  );
}

// ============================================================
// Объявления (06-market-user-listings.md §6): размещения за слоты пакетов
// ============================================================

/** Валидация года/цены лота — те же правила, что у user-формы «Продать». */
const LOT_YEAR_MIN = 1980;
const LOT_YEAR_MAX = 2027;

const MKT_CURRENCIES: { value: "KRW" | "USD" | "RUB"; label: string }[] = [
  { value: "KRW", label: "₩ KRW" },
  { value: "USD", label: "$ USD" },
  { value: "RUB", label: "₽ RUB" },
];

/** Топливо/КПП: значения совпадают со словарём мини-аппа (lib/specs). */
const MKT_FUELS: { value: string; label: string }[] = [
  { value: "petrol", label: "Бензин" },
  { value: "diesel", label: "Дизель" },
  { value: "hybrid", label: "Гибрид" },
  { value: "electric", label: "Электро" },
  { value: "lpg", label: "Газ" },
];

const MKT_TRANSMISSIONS: { value: string; label: string }[] = [
  { value: "auto", label: "АКПП" },
  { value: "manual", label: "МКПП" },
];

interface LotFormErrors {
  make?: string;
  model?: string;
  year?: string;
  price?: string;
}

function validateLot(
  make: string,
  model: string,
  year: string,
  price: string,
): LotFormErrors {
  const e: LotFormErrors = {};
  if (!make.trim()) e.make = "Укажите марку";
  if (!model.trim()) e.model = "Укажите модель";
  const y = Number(year);
  if (!year.trim()) e.year = "Укажите год выпуска";
  else if (!Number.isInteger(y) || y < LOT_YEAR_MIN || y > LOT_YEAR_MAX)
    e.year = `Год — от ${LOT_YEAR_MIN} до ${LOT_YEAR_MAX}`;
  const p = Number(price);
  if (!price.trim()) e.price = "Укажите цену";
  else if (!Number.isFinite(p) || p <= 0) e.price = "Цена должна быть больше нуля";
  return e;
}

/**
 * Раздел «Объявления» (после Витрины): покупка пакетов размещений со
 * баланса (402 — денег нет), форма размещения лота (409 — слоты кончились)
 * и список размещённых со снятием. Слоты расходуются на публикацию и не
 * возвращаются (§1).
 */
export function PartnerAds({
  onUnauthorized,
  onChanged,
}: {
  onUnauthorized: () => void;
  onChanged: () => void;
}) {
  const toast = usePToast();

  // Пакеты размещений
  const [packages, setPackages] = useState<MarketPackage[] | null>(null);
  const [packagesErr, setPackagesErr] = useState<string | null>(null);
  /** Свободные места: известны после покупки пакета (GET слотов нет). */
  const [slots, setSlots] = useState<number | null>(null);
  const [buyingSize, setBuyingSize] = useState<number | null>(null);

  // Мои размещённые лоты
  const [lots, setLots] = useState<MyLot[] | null>(null);
  const [lotsErr, setLotsErr] = useState<string | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);

  // Форма «Разместить лот»
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [year, setYear] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState<"KRW" | "USD" | "RUB">("KRW");
  const [mileage, setMileage] = useState("");
  const [fuel, setFuel] = useState("");
  const [transmission, setTransmission] = useState("");
  const [description, setDescription] = useState("");
  const [formErrors, setFormErrors] = useState<LotFormErrors>({});
  const [placing, setPlacing] = useState(false);

  const load = useCallback(() => {
    fetchMarketPackages()
      .then((list) => setPackages(Array.isArray(list) ? list : []))
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setPackagesErr(partnerFail(e).text);
      });
    fetchPartnerLots()
      .then((list) => setLots(Array.isArray(list) ? list : []))
      .catch((e) => {
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setLotsErr(partnerFail(e).text);
      });
  }, [onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  /** Покупка пакета: подтверждение → списание с баланса → слоты. */
  const buy = async (p: MarketPackage) => {
    if (buyingSize != null) return;
    if (!window.confirm(`Купить пакет на ${p.size} размещений за ${p.price_rub.toLocaleString("ru-RU")} ₽?`)) return;
    setBuyingSize(p.size);
    try {
      const r = await buyMarketPackage(p.size);
      // PackageOut: slots_free — свободные места после покупки (аудит 06)
      setSlots(r.slots_free ?? (slots ?? 0) + p.size);
      toast.show(`Пакет активирован: ${p.size} мест`);
      onChanged(); // баланс в шапке обновится через /me
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else if (e instanceof PartnerApiError && e.status === 402)
        toast.show("Недостаточно средств на балансе. Пополните счёт у менеджера.", { error: true });
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setBuyingSize(null);
    }
  };

  /** Разместить лот: та же валидация, что у user-формы; слот — на бэке. */
  const place = async () => {
    if (placing) return;
    const errs = validateLot(make, model, year, price);
    setFormErrors(errs);
    if (Object.keys(errs).length > 0) return;
    setPlacing(true);
    try {
      await addPartnerLot({
        make: make.trim(),
        model: model.trim(),
        year: Number(year),
        price: Number(price),
        currency,
        ...(mileage.trim() ? { mileage_km: Number(mileage) } : {}),
        ...(fuel ? { fuel } : {}),
        ...(transmission ? { transmission } : {}),
        ...(description.trim() ? { description: description.trim().slice(0, 2000) } : {}),
      });
      toast.show("✅ Лот размещён — он уже в ленте рынка");
      setMake("");
      setModel("");
      setYear("");
      setPrice("");
      setMileage("");
      setFuel("");
      setTransmission("");
      setDescription("");
      setFormErrors({});
      if (slots != null) setSlots(Math.max(0, slots - 1));
      load();
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else if (e instanceof PartnerApiError && e.status === 409)
        toast.show("Свободных мест нет — купите пакет размещений", { error: true });
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setPlacing(false);
    }
  };

  /** Снять размещение: слот пакета не возвращается (§1) — подтверждение. */
  const remove = async (lotId: number) => {
    if (removing != null) return;
    if (!window.confirm("Снять объявление? Место пакета не возвращается.")) return;
    setRemoving(lotId);
    try {
      await removePartnerLot(lotId);
      toast.show("Объявление снято");
      load();
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setRemoving(null);
    }
  };

  return (
    <>
      <h1 className="pa-title">Объявления</h1>
      <p className="pa-footnote">
        Размещайте свои авто в ленте рынка: покупатели видят их с бейджем
        «🏪 Салон» и пишут вам в чаты. Размещения расходуют места пакетов.
      </p>

      {/* Пакеты размещений (§2): карточки size/цена, покупка со баланса */}
      <section className="pa-section">
        <div className="pa-section__head">
          <h2 className="pa-section__title">Пакеты размещений</h2>
          <span className="pa-mkt-slots">
            Свободно мест: <b>{slots ?? "—"}</b>
          </span>
        </div>
        {packagesErr ? (
          <ErrorBlock description={packagesErr} onAction={load} />
        ) : packages === null ? (
          <SkelRows n={2} />
        ) : packages.length === 0 ? (
          <EmptyBlock
            title="Пакеты недоступны"
            description="Каталог пакетов не настроен — напишите менеджеру."
          />
        ) : (
          <div className="pa-mkt-packages">
            {packages.map((p) => (
              <div key={p.size} className="pa-mkt-package">
                <div className="pa-mkt-package__size">
                  <b>{p.size}</b> мест
                </div>
                <div className="pa-mkt-package__price">
                  {p.price_rub.toLocaleString("ru-RU")} ₽
                </div>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={buyingSize != null}
                  onClick={() => void buy(p)}
                >
                  {buyingSize === p.size ? "Покупаем…" : "Купить"}
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="pa-footnote">Списание с баланса счёта, места — бессрочные.</p>
      </section>

      {/* Форма «Разместить лот»: те же поля, что у user-размещения (§2) */}
      <section className="pa-section">
        <h2 className="pa-section__title">Разместить лот</h2>
        <form
          className="pa-mkt-form"
          onSubmit={(e) => {
            e.preventDefault();
            void place();
          }}
          noValidate
        >
          <label className="pa-mkt-field">
            <span>Марка *</span>
            <input
              className="input"
              placeholder="Kia"
              aria-label="Марка"
              value={make}
              onChange={(e) => setMake(e.target.value)}
              maxLength={60}
            />
            {formErrors.make && <em className="pa-mkt-field__error">{formErrors.make}</em>}
          </label>
          <label className="pa-mkt-field">
            <span>Модель *</span>
            <input
              className="input"
              placeholder="K5"
              aria-label="Модель"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              maxLength={60}
            />
            {formErrors.model && <em className="pa-mkt-field__error">{formErrors.model}</em>}
          </label>
          <label className="pa-mkt-field">
            <span>Год выпуска *</span>
            <input
              className="input"
              inputMode="numeric"
              placeholder="2022"
              aria-label="Год выпуска"
              value={year}
              onChange={(e) => setYear(digits(e.target.value).slice(0, 4))}
            />
            {formErrors.year && <em className="pa-mkt-field__error">{formErrors.year}</em>}
          </label>
          <label className="pa-mkt-field">
            <span>Цена *</span>
            <input
              className="input"
              inputMode="numeric"
              placeholder="20 000 000"
              aria-label="Цена"
              value={price}
              onChange={(e) => setPrice(digits(e.target.value).slice(0, 12))}
            />
            {formErrors.price && <em className="pa-mkt-field__error">{formErrors.price}</em>}
          </label>
          <label className="pa-mkt-field">
            <span>Валюта *</span>
            <select
              className="input"
              aria-label="Валюта"
              value={currency}
              onChange={(e) => setCurrency(e.target.value as "KRW" | "USD" | "RUB")}
            >
              {MKT_CURRENCIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          <label className="pa-mkt-field">
            <span>Пробег, км</span>
            <input
              className="input"
              inputMode="numeric"
              placeholder="35 000"
              aria-label="Пробег, км"
              value={mileage}
              onChange={(e) => setMileage(digits(e.target.value).slice(0, 9))}
            />
          </label>
          <label className="pa-mkt-field">
            <span>Топливо</span>
            <select
              className="input"
              aria-label="Топливо"
              value={fuel}
              onChange={(e) => setFuel(e.target.value)}
            >
              <option value="">Не выбрано</option>
              {MKT_FUELS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label className="pa-mkt-field">
            <span>КПП</span>
            <select
              className="input"
              aria-label="КПП"
              value={transmission}
              onChange={(e) => setTransmission(e.target.value)}
            >
              <option value="">Не выбрано</option>
              {MKT_TRANSMISSIONS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="pa-mkt-field pa-mkt-field--wide">
            <span>Описание (до 2000)</span>
            <textarea
              className="input"
              rows={3}
              maxLength={2000}
              placeholder="Комплектация, состояние, гарантия…"
              aria-label="Описание"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <button type="submit" className="btn btn--primary" disabled={placing}>
            {placing ? "Размещаем…" : "Разместить"}
          </button>
        </form>
      </section>

      {/* Мои размещённые лоты: «Размещено» (слот), снятие без возврата слота */}
      <section className="pa-section">
        <h2 className="pa-section__title">Мои объявления</h2>
        {lotsErr ? (
          <ErrorBlock description={lotsErr} onAction={load} />
        ) : lots === null ? (
          <SkelRows n={2} />
        ) : lots.length === 0 ? (
          <EmptyBlock
            title="Размещений пока нет"
            description="Разместите первый лот — он появится в ленте рынка с бейджем салона."
          />
        ) : (
          <div className="pa-mkt-lots">
            {lots.map((lot) => {
              const name = `${lot.make ?? ""} ${lot.model ?? ""}`.trim() || `Лот #${lot.listing_id}`;
              return (
                <article key={lot.listing_id} className="pa-mkt-lot">
                  {lot.images?.[0] ? (
                    <img className="pa-mkt-lot__img" src={lot.images[0]} alt="" loading="lazy" />
                  ) : (
                    <span className="pa-mkt-lot__img pa-mkt-lot__img--empty" aria-hidden="true">
                      🚗
                    </span>
                  )}
                  <div className="pa-mkt-lot__main">
                    <span className="pa-mkt-lot__name">
                      {name}
                      {lot.year ? `, ${lot.year}` : ""}
                    </span>
                    <span className="pa-mkt-lot__meta">
                      #{lot.listing_id}
                      {lot.price != null && ` · ${fmtMoneyCompact(lot.price, lot.currency)}`}
                      {lot.mileage_km != null && ` · ${lot.mileage_km.toLocaleString("ru-RU")} км`}
                    </span>
                    <span className="pa-pill pa-pill--accepted">
                      <span className="pa-pill__dot" />
                      {lot.placement_status === "slot" ? "Размещено" : "На паузе"}
                    </span>
                  </div>
                  <div className="pa-mkt-lot__actions">
                    <a
                      className="btn pa-btn-sm"
                      href={`/listing/${lot.listing_id}`}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      Карточка
                    </a>
                    <button
                      type="button"
                      className="btn btn--danger pa-btn-sm"
                      disabled={removing === lot.listing_id}
                      onClick={() => void remove(lot.listing_id)}
                    >
                      {removing === lot.listing_id ? "Снимаем…" : "Снять"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </>
  );
}
