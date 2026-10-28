/**
 * «История» (меню v3, 2026-10-25): сегменты «Просмотры» и «Разборы».
 *  - Просмотры: открытые карточки (lib/viewHistory, localStorage —
 *    работает и гостю; записи пишет ListingScreen событием listing:view);
 *  - Разборы: история AI-разборов с verdict-бейджами (03 §2.8);
 *  - «Похожие машины»: deep-link /analyses?similar={id} (в т.ч. из
 *    уведомления бота «продана — ищем похожие»);
 *  - квота исчерпана (403 quota_exceeded) — апселл «⚡ Безлимит в Pro».
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { haptic, isGuest } from "../api/auth";
import { getRegion, priceChain } from "../lib/fx";
import { VERDICT_LABEL } from "../lib/analysis";
import {
  clearViews,
  getViews,
  subscribeViews,
  type ViewedListing,
} from "../lib/viewHistory";
import { useToast } from "../ui/Toast";
import type { MyAnalysis, SimilarItem, SimilarResponse } from "../api/types";
import { apiErrorView } from "./apiErrorText";

type Segment = "views" | "analyses";

/** SR-текст вердикта (03 §5.5): цвет дублируется словом. */
const VERDICT_SR: Record<MyAnalysis["verdict"], string> = {
  deal: "Вердикт: выгодная цена",
  fair: "Вердикт: справедливая цена",
  overpriced: "Вердикт: выше рынка",
};

/** Бейдж вердикта: deal — выгода, fair — в рынке, overpriced — дорого. */
function VerdictBadge({ verdict }: { verdict: MyAnalysis["verdict"] }) {
  return (
    <span className={`verdict-badge verdict-badge--${verdict}`} aria-label={VERDICT_SR[verdict]}>
      <span aria-hidden="true">{VERDICT_LABEL[verdict].emoji}</span>{" "}
      {VERDICT_LABEL[verdict].text}
    </span>
  );
}

/** Цена в валюте региона (как в ленте), фолбэк — валюта источника. */
function localPrice(price: number | null, currency: string | null): string {
  const chain = priceChain(price, currency, getRegion());
  return chain.local ?? chain.original ?? "—";
}

interface SimilarState {
  forId: number;
  source: "ai" | "stats";
  items: SimilarItem[];
}

export function AnalysesScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const [guest, setGuest] = useState(() => isGuest());
  const [segment, setSegment] = useState<Segment>("views");
  const [views, setViews] = useState<ViewedListing[]>(() => getViews());
  const [analyses, setAnalyses] = useState<MyAnalysis[] | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [similar, setSimilar] = useState<SimilarState | null>(null);
  const [similarBusy, setSimilarBusy] = useState<number | null>(null);
  const [searchParams] = useSearchParams();
  const requestedSimilar = useRef<number | null>(null);

  // Просмотры живые: обновляются событием из ListingScreen и чисткой.
  useEffect(() => subscribeViews(() => setViews(getViews())), []);

  // Смена пользователя (гость вошёл / юзер вышел): ключ истории
  // autobot.views.{sub} меняется, но keep-alive экран не перемонтируется —
  // перечитываем гостя и просмотры, чтобы не показать чужую историю
  // (и наоборот: заглушку «Войдите» — залогиненному).
  useEffect(() => {
    const onAuth = () => {
      setGuest(isGuest());
      setViews(getViews());
    };
    window.addEventListener("autobot:auth", onAuth);
    return () => window.removeEventListener("autobot:auth", onAuth);
  }, []);

  const load = useCallback(() => {
    setLoadError(null);
    api<MyAnalysis[]>("/me/analyses?limit=50")
      .then(setAnalyses)
      .catch((e: unknown) => {
        setAnalyses([]);
        setLoadError(e);
      });
  }, []);

  useEffect(() => {
    if (!guest && segment === "analyses") load();
  }, [guest, segment, load]);

  /** POST similar: результаты показываем секцией сверху экрана. */
  const findSimilar = useCallback(
    async (listingId: number) => {
      if (similarBusy !== null) return;
      setSimilarBusy(listingId);
      haptic("medium");
      try {
        const res = await api<SimilarResponse>(`/listings/${listingId}/similar`, {
          method: "POST",
        });
        setSimilar({ forId: listingId, source: res.source, items: res.items });
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch {
        toast.show("Похожие машины сейчас недоступны — попробуйте позже", {
          error: true,
        });
      } finally {
        setSimilarBusy(null);
      }
    },
    [similarBusy, toast],
  );

  // Deep-link из карточки объявления: /analyses?similar={id} — сразу ищем.
  // Guard по id (а не «раз за монтаж»): экран в keep-alive и не
  // перемонтируется, повторный deep-link с другим id должен срабатывать.
  useEffect(() => {
    if (guest) return;
    const q = searchParams.get("similar");
    if (!q || !/^\d+$/.test(q)) return;
    const id = Number(q);
    if (requestedSimilar.current === id) return;
    requestedSimilar.current = id;
    void findSimilar(id);
  }, [guest, searchParams, findSimilar]);

  if (guest && segment === "analyses") {
    return (
      <div className="screen">
        <Segmented onSegment={setSegment} segment={segment} />
        <div className="state state--panel">
          <div className="state__emoji">🔐</div>
          <div className="state__title">Войдите, чтобы сохранять анализы</div>
          <div className="state__text">
            История AI-разборов цены и поиск похожих машин привязаны к
            аккаунту — так они не потеряются между сессиями.
          </div>
          <button
            className="btn btn--primary"
            onClick={() => navigate("/login")}
          >
            Войти
          </button>
        </div>
        {/* L3: замоканный пример вердикта — показать ценность до входа */}
        <div className="an-demo" aria-hidden="true">
          <div className="an-demo__row">
            🟢 Выгодно
            <span className="verdict-badge verdict-badge--deal">−7% к рынку</span>
          </div>
          <div className="an-demo__meta">
            Hyundai Palisade, 2023 · $42 900 · рынок $44–48 тыс
          </div>
        </div>
      </div>
    );
  }

  /** Ошибка загрузки: 403-квота — апселл, прочее — ретрай (03 §3.2). */
  const errorView = loadError != null ? apiErrorView(loadError) : null;

  return (
    <div className="screen">
      <Segmented onSegment={setSegment} segment={segment} />

      {/* Похожие машины: результаты поверх истории, чтобы были на виду */}
      {similar && (
        <section className="panel">
          <h3>
            Похожие машины{" "}
            <span className="similar-source">
              {similar.source === "ai" ? "🤖 нейросеть" : "📊 статистика"}
            </span>
          </h3>
          {similar.items.length === 0 ? (
            <div className="state state--panel">
              <div className="state__emoji">🔎</div>
              <div className="state__text">
                Похожих машин не нашлось — база пополняется каждый день,
                попробуйте позже.
              </div>
            </div>
          ) : (
            <div className="similar-list">
              {similar.items.map((s) => (
                <article
                  key={s.id}
                  className="similar-item"
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/listing/${s.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(`/listing/${s.id}`);
                    }
                  }}
                >
                  {s.image ? (
                    <img className="similar-item__img" src={s.image} alt="" loading="lazy" />
                  ) : (
                    <div className="similar-item__img similar-item__img--empty">🚗</div>
                  )}
                  <div className="similar-item__body">
                    <div className="similar-item__title">
                      {`${s.make ?? ""} ${s.model ?? ""}`.trim() || "Объявление"}
                      {s.year ? `, ${s.year}` : ""}
                    </div>
                    <div className="similar-item__meta">
                      {localPrice(s.price, s.currency)}
                      {s.mileage_km != null &&
                        ` · ${s.mileage_km.toLocaleString("ru")} км`}
                    </div>
                    {s.reason && (
                      <div className="similar-item__reason">💬 {s.reason}</div>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          <button className="btn" onClick={() => setSimilar(null)}>
            Скрыть результаты
          </button>
        </section>
      )}

      {/* Просмотры: открытые карточки (localStorage, доступно гостю) */}
      {segment === "views" && (
        <section className="panel">
          <div className="hist-head">
            <h3>Просмотры</h3>
            {views.length > 0 && (
              <button className="btn" onClick={() => clearViews()}>
                Очистить историю
              </button>
            )}
          </div>
          {views.length === 0 ? (
            <div className="state state--panel">
              <div className="state__emoji">🕘</div>
              <div className="state__title">Вы пока ничего не смотрели</div>
              <div className="state__text">
                Здесь появятся машины, карточки которых вы открывали —
                вернуться к просмотру можно в один тап.
              </div>
              <button className="btn btn--primary" onClick={() => navigate("/")}>
                К ленте
              </button>
            </div>
          ) : (
            <div className="similar-list">
              {views.map((v) => (
                <article
                  key={v.listing_id}
                  className="similar-item"
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/listing/${v.listing_id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(`/listing/${v.listing_id}`);
                    }
                  }}
                >
                  {v.image ? (
                    <img className="similar-item__img" src={v.image} alt="" loading="lazy" />
                  ) : (
                    <div className="similar-item__img similar-item__img--empty">🚗</div>
                  )}
                  <div className="similar-item__body">
                    <div className="similar-item__title">
                      {`${v.make ?? ""} ${v.model ?? ""}`.trim() || "Объявление"}
                      {v.year ? `, ${v.year}` : ""}
                    </div>
                    <div className="similar-item__meta">
                      {localPrice(v.price ?? null, v.currency ?? null)} ·{" "}
                      {new Date(v.viewed_at).toLocaleString("ru", {
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {/* История AI-анализов */}
      {segment === "analyses" && (
      <section className="panel">
        <h3>Мои анализы</h3>
        {errorView && (
          <div className="state state--panel">
            <div className="state__emoji">⚠️</div>
            <div className="state__title">Не удалось загрузить историю</div>
            <div className="state__text">{errorView.text}</div>
            {/* Квота исчерпана → апселл; прочие ошибки → повторить */}
            {errorView.action ? (
              <Link className="btn" to={errorView.action.to ?? "/profile"}>
                {errorView.action.label}
              </Link>
            ) : (
              <button className="btn" onClick={load}>
                Повторить
              </button>
            )}
          </div>
        )}
        {!errorView && analyses === null && (
          <div className="state">
            <span className="skeleton" style={{ width: 200, height: 60 }} />
          </div>
        )}
        {!errorView && analyses?.length === 0 && (
          <div className="state state--panel">
            <div className="state__emoji">🤖</div>
            <div className="state__title">Анализов пока нет</div>
            <div className="state__text">
              Откройте объявление в ленте и запустите AI-анализ цены:
              нейросеть сравнит её с рынком и вынесет вердикт. Все разборы
              сохранятся здесь.
            </div>
            <button className="btn btn--primary" onClick={() => navigate("/")}>
              К ленте
            </button>
          </div>
        )}
        {analyses && analyses.length > 0 && (
          <div className="analysis-list">
            {analyses.map((a) => (
              <article className="analysis-card" key={a.id}>
                <div className="analysis-card__head">
                  {a.image ? (
                    <img className="analysis-card__img" src={a.image} alt="" loading="lazy" />
                  ) : (
                    <div className="analysis-card__img analysis-card__img--empty">🚗</div>
                  )}
                  <div className="analysis-card__body">
                    <div className="analysis-card__title">
                      {`${a.make ?? ""} ${a.model ?? ""}`.trim() || "Объявление"}
                      {a.year ? `, ${a.year}` : ""}
                    </div>
                    <div className="analysis-card__meta">
                      {localPrice(a.price, a.currency)} ·{" "}
                      {new Date(a.created_at).toLocaleDateString("ru")}
                    </div>
                    <VerdictBadge verdict={a.verdict} />
                    {a.summary && (
                      <p className="analysis-card__summary">{a.summary}</p>
                    )}
                  </div>
                </div>
                <div className="analysis-card__actions">
                  <Link className="btn" to={`/listing/${a.listing_id}`}>
                    Открыть карточку
                  </Link>
                  <button
                    className="btn"
                    disabled={similarBusy === a.listing_id}
                    onClick={() => void findSimilar(a.listing_id)}
                  >
                    {similarBusy === a.listing_id ? "Ищем…" : "Найти похожие"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      )}
    </div>
  );
}

/** Переключатель сегментов «Просмотры / Разборы» (стили .hist-seg-*). */
function Segmented({
  segment,
  onSegment,
}: {
  segment: Segment;
  onSegment: (s: Segment) => void;
}) {
  const items: { key: Segment; label: string }[] = [
    { key: "views", label: "Просмотры" },
    { key: "analyses", label: "Разборы" },
  ];
  return (
    <div className="hist-seg" role="tablist" aria-label="Разделы истории">
      {items.map((it) => (
        <button
          key={it.key}
          role="tab"
          type="button"
          aria-selected={segment === it.key}
          className={segment === it.key ? "hist-seg__btn hist-seg__btn--active" : "hist-seg__btn"}
          onClick={() => onSegment(it.key)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
