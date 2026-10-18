/** Карточка объявления (UI/UX ТЗ §2.3): галерея, характеристики, избранное. */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Heart, ExternalLink, Truck } from "lucide-react";

import { api } from "../api/client";
import { haptic, isGuest } from "../api/auth";
import { useToast } from "../ui/Toast";
import { useLoginPrompt } from "../ui/LoginPrompt";
import { SkeletonCard } from "../ui/Skeleton";
import { ErrorState } from "../ui/States";
import type { Listing } from "../api/types";
import { getRegion, priceChain, toUsd } from "../lib/fx";
import { VERDICT_LABEL, deltaPct, fairPosition, type AnalysisData } from "../lib/analysis";
import { PriceChart } from "../ui/PriceChart";
import { InspectionSection } from "../components/Inspection";
import type { InspectionReport } from "../lib/inspection";

export function ListingScreen() {
  const { id } = useParams<{ id: string }>();
  const [listing, setListing] = useState<Listing | null>(null);
  const [favorited, setFavorited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisData | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [quotaHit, setQuotaHit] = useState(false);
  const [inspection, setInspection] = useState<InspectionReport | null>(null);
  const navigate = useNavigate();
  const toast = useToast();
  const loginPrompt = useLoginPrompt();

  useEffect(() => {
    api<Listing>(`/listings/${id}`)
      .then(setListing)
      .catch((e: Error) => setError(e.message));
    api<{ favorited: boolean }>(`/favorites/${id}`)
      .then((r) => setFavorited(r.favorited))
      .catch(() => undefined); // вне Telegram — без избранного
    // Осмотр (0018): если отчёта нет — блок просто не показываем (404 молча)
    api<InspectionReport>(`/listings/${id}/inspection`)
      .then(setInspection)
      .catch(() => undefined);
    // Кэш последнего разбора (без LLM): если есть — сразу график.
    // Гостю аналитика недоступна — не запрашиваем вовсе.
    if (!isGuest()) {
      api<AnalysisData | null>(`/listings/${id}/analysis`)
        .then((a) => a && setAnalysis(a))
        .catch(() => undefined);
    }
  }, [id]);

  const runAnalysis = async () => {
    if (analyzing) return;
    if (isGuest()) {
      // Аналитика — только для авторизированных (ТЗ: гость = демо без LLM)
      loginPrompt.show(
        "AI-анализ цены доступен после входа — войдите или зарегистрируйтесь.",
      );
      return;
    }
    setAnalyzing(true);
    haptic("medium");
    try {
      const a = await api<AnalysisData>(`/listings/${id}/analysis`, {
        method: "POST",
      });
      setAnalysis(a);
      haptic("heavy");
    } catch (e) {
      if ((e as { status?: number }).status === 403) {
        setQuotaHit(true);
      } else {
        toast.show("Анализ недоступен, попробуйте позже", { error: true });
      }
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleFavorite = async () => {
    haptic("medium");
    try {
      const r = await api<{ favorited: boolean }>(`/listings/${id}/favorite`, {
        method: "POST",
      });
      setFavorited(r.favorited);
      toast.show(r.favorited ? "❤️ Добавлено в избранное" : "Убрано из избранного");
    } catch {
      toast.show("Не удалось изменить избранное", { error: true });
    }
  };

  if (error) {
    return <ErrorState description={error} onRetry={() => navigate(-1)} />;
  }
  if (!listing) {
    return (
      <div style={{ padding: 12 }}>
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }

  const title = `${listing.make ?? ""} ${listing.model ?? ""}`.trim();
  // KRW/EUR → USD по фикс-курсу MVP (как в боте), иначе в калькулятор уйдут воны
  const usd = toUsd(listing.price, listing.currency);
  const chain = priceChain(listing.price, listing.currency, getRegion());

  return (
    <div className="listing-page">
      {/* Назад — всегда под рукой, поверх галереи (не только внизу страницы) */}
      <button className="listing-back" aria-label="Назад" onClick={() => navigate(-1)}>
        ‹
      </button>
      <div className="listing-page__main">
        {/* Галерея: свайп + счётчик + фуллскрин по клику (ТЗ §2.3) */}
        {listing.images.length > 0 ? (
          <Gallery images={listing.images} alt={title} />
        ) : (
          <div className="state">
            <div className="state__emoji">📷</div>
            <div className="state__text">Фото не загружены</div>
          </div>
        )}

        {/* Характеристики — карточка дизайн-системы вместо telegram-ui Section */}
        <section className="panel spec-card">
          <h3>{title}</h3>
          <div className="spec-card__price">
            {chain.usd
              ? `${chain.usd}${chain.local ? ` · ${chain.local}` : ""}`
              : chain.original ?? "—"}
          </div>
          {chain.original && (
            <div className="info-row">
              <span className="info-row__label">Цена у источника</span>
              <span className="info-row__value">{chain.original}</span>
            </div>
          )}
          <div className="info-row">
            <span className="info-row__label">Год</span>
            <span className="info-row__value">{listing.year ?? "—"}</span>
          </div>
          <div className="info-row">
            <span className="info-row__label">Пробег</span>
            <span className="info-row__value">
              {listing.mileage_km ? `${listing.mileage_km.toLocaleString("ru")} км` : "—"}
            </span>
          </div>
          {usd != null && (listing.currency ?? "").toUpperCase() !== "USD" && (
            <div className="info-row">
              <span className="info-row__label">По курсу (оценка)</span>
              <span className="info-row__value">≈ {usd.toLocaleString("ru")} $</span>
            </div>
          )}
          <div className="info-row">
            <span className="info-row__label">VIN</span>
            <span className="info-row__value">{listing.vin ?? "не указан"}</span>
          </div>
          <div className="info-row">
            <span className="info-row__label">Источник</span>
            <span className="info-row__value">{listing.source}</span>
          </div>
          <div className="info-row">
            <span className="info-row__label">Обновлено</span>
            <span className="info-row__value">
              {new Date(listing.parsed_at).toLocaleDateString("ru")}
            </span>
          </div>
        </section>

      </div>

      <div className="listing-page__side">
      {/* Название с источника (PLAN.md, фаза 5): перевод, иначе оригинал */}
      {(listing.title_ru ?? listing.title_raw) && (
        <section className="panel">
          <div className="info-row">
            <span className="info-row__label">Название с источника</span>
            <span className="info-row__value">{listing.title_ru ?? listing.title_raw}</span>
          </div>
        </section>
      )}

      {/* AI-анализ стоимости (монетизация: 1/нед бесплатно, далее Pro);
          гостям закрыт — только после входа/регистрации */}
      <section className="panel">
        <h3>🤖 AI-анализ цены</h3>
        {analyzing ? (
          <div className="analysis">
            <div className="analysis__loading">
              <span className="analysis__spinner" />
              Сравниваю с рынком…
              <div className="analysis__loading-sub">
                сопоставимые объявления, история цены, вердикт — до минуты
              </div>
            </div>
            <div className="skeleton" style={{ height: 64, borderRadius: 12 }} />
            <div className="skeleton" style={{ height: 96, borderRadius: 12 }} />
          </div>
        ) : analysis ? (
          <div className="analysis">
            <div className="analysis__head">
              <div
                className={`analysis__verdict analysis__verdict--${analysis.verdict}`}
              >
                {VERDICT_LABEL[analysis.verdict].emoji}{" "}
                {VERDICT_LABEL[analysis.verdict].text}
              </div>
              {deltaPct(analysis) != null && (
                <span
                  className={`analysis__pill analysis__pill--${
                    deltaPct(analysis)! <= 0 ? "good" : "bad"
                  }`}
                >
                  {deltaPct(analysis)! > 0 ? "▲" : "▼"} {deltaPct(analysis)}%
                </span>
              )}
            </div>
            {analysis.fair_min != null && analysis.fair_max != null && (
              <div className="analysis__range">
                <div className="analysis__range-label">
                  Справедливый диапазон:{" "}
                  <b>
                    {Math.round(analysis.fair_min).toLocaleString("ru-RU")}–
                    {Math.round(analysis.fair_max).toLocaleString("ru-RU")}{" "}
                    {analysis.currency}
                  </b>
                </div>
                {fairPosition(analysis) != null && (
                  <div className="analysis__bar">
                    <div
                      className="analysis__bar-marker"
                      style={{ left: `${fairPosition(analysis)}%` }}
                    />
                  </div>
                )}
              </div>
            )}
            {analysis.history.length > 1 ? (
              <PriceChart history={analysis.history} comps={analysis.comps} />
            ) : (
              <div className="analysis__hint">
                📈 Истории цены пока мало — график появится, когда начнём
                отслеживать объявление.
              </div>
            )}
            {analysis.summary && (
              <div className="analysis__summary">💬 {analysis.summary}</div>
            )}
            {analysis.factors.length > 0 && (
              <ul className="analysis__factors">
                {analysis.factors.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            )}
            {analysis.comps && (
              <div className="analysis__market">
                Рынок: {analysis.comps.count} сопоставимых · медиана{" "}
                {analysis.comps.median.toLocaleString("ru-RU")} {analysis.currency}
              </div>
            )}
            <div className="analysis__meta">
              {analysis.created_at &&
                `разбор от ${new Date(analysis.created_at).toLocaleDateString("ru")}`}
              {" · "}
              {analysis.source === "ai" ? "нейросеть" : "статистика рынка"}
              <button className="analysis__retry" onClick={() => void runAnalysis()}>
                🔄 Пересчитать
              </button>
            </div>
          </div>
        ) : quotaHit ? (
          <div className="analysis analysis--locked">
            🔒 Бесплатный разбор этой машины на этой неделе уже использован —
            он доступен снова через несколько дней.
            <button
              className="btn btn--primary btn--block"
              style={{ marginTop: 10 }}
              onClick={() => {
                haptic();
                navigate("/profile");
              }}
            >
              ⚡ Безлимит в Pro
            </button>
          </div>
        ) : (
          <div className="analysis">
            <div className="analysis__hint">
              {isGuest()
                ? "🔒 Нейросеть сравнит цену с рынком: сопоставимые объявления, история цены, вердикт. Доступно после входа."
                : "Нейросеть сравнит цену с рынком: сопоставимые объявления, история цены, вердикт и факторы."}
            </div>
            <button
              className="btn btn--primary btn--block"
              style={{ marginTop: 10 }}
              onClick={() => void runAnalysis()}
            >
              {isGuest()
                ? "🤖 Анализировать — по входу"
                : "🤖 Анализировать (1 бесплатно в неделю)"}
            </button>
          </div>
        )}
      </section>

      {/* Осмотр (миграция 0018): карта повреждений + чек-лист, только если есть отчёт */}
      {inspection && (
        <section className="panel">
          <h3>🔍 Осмотр</h3>
          <InspectionSection report={inspection} />
        </section>
      )}

      {/* История цен (PLAN.md, фаза 4): точки listing_price_history, новые сверху */}
      {listing.price_history && listing.price_history.length > 1 && (
        <section className="panel">
          <h3>История цен</h3>
          {[...listing.price_history].reverse().map((p, i) => (
            <div className="info-row" key={`${p.recorded_at}-${i}`}>
              <span className="info-row__label">
                {new Date(p.recorded_at).toLocaleDateString("ru")}
                {i === 0 ? " · текущая" : ""}
              </span>
              <span className="info-row__value">
                <b>
                  {p.price.toLocaleString("ru")} {p.currency ?? ""}
                </b>
              </span>
            </div>
          ))}
        </section>
      )}
      {listing.is_active === false && (
        <div className="state">
          <div className="state__emoji">🔴</div>
          <div className="state__title">Исчезло с продажи</div>
          <div className="state__text">Объявления больше нет на источнике</div>
        </div>
      )}

      {/* CTA-блок: primary один — «Открыть у источника» (e-commerce стандарт) */}
      <button
        className="btn btn--primary btn--block"
        onClick={() => {
          haptic();
          window.open(listing.url, "_blank");
        }}
      >
        <ExternalLink size={18} />
        Открыть у источника
      </button>
      <button
        className="btn btn--block"
        onClick={() => {
          haptic();
          navigate(
            `/calc?${usd != null ? `price=${usd}&` : ""}year=${listing.year ?? ""}&cur=${encodeURIComponent(listing.currency ?? "")}`,
          );
        }}
      >
        <Truck size={18} />
        Доставка и растаможка
      </button>
      <button className="btn btn--block" onClick={() => void toggleFavorite()}>
        <Heart
          size={18}
          fill={favorited ? "var(--accent)" : "none"}
          color={favorited ? "var(--accent)" : undefined}
        />
        {favorited ? "В избранном" : "В избранное"}
      </button>
      <button className="btn btn--block" onClick={() => navigate(-1)}>
        Назад
      </button>
      </div>

      {/* Sticky-CTA на мобиле: цена + избранное + главное действие под пальцем */}
      <div className="listing-cta-bar">
        <button
          className="listing-cta-bar__fav"
          aria-label={favorited ? "Убрать из избранного" : "В избранное"}
          aria-pressed={favorited}
          onClick={() => void toggleFavorite()}
        >
          <Heart
            size={22}
            fill={favorited ? "var(--accent)" : "none"}
            color={favorited ? "var(--accent)" : "currentColor"}
          />
        </button>
        <div className="listing-cta-bar__price">
          {chain.usd ?? chain.original ?? "—"}
        </div>
        <button
          className="listing-cta-bar__btn"
          onClick={() => {
            haptic();
            window.open(listing.url, "_blank");
          }}
        >
          Открыть у источника
        </button>
      </div>
    </div>
  );
}

function Gallery({ images, alt }: { images: string[]; alt: string }) {
  const [idx, setIdx] = useState(0);
  const [full, setFull] = useState<number | null>(null);

  const show = useCallback((i: number) => {
    haptic("light");
    setFull(((i % images.length) + images.length) % images.length);
  }, [images.length]);

  // Лайтбокс: Esc/стрелки с клавиатуры (ПК), блокировка скролла фона
  useEffect(() => {
    if (full == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFull(null);
      if (e.key === "ArrowRight") show((full ?? 0) + 1);
      if (e.key === "ArrowLeft") show((full ?? 0) - 1);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [full, show]);

  const thumbs = images.slice(0, 6);
  const rest = images.length - thumbs.length;

  return (
    <div className="gallery">
      <div
        className="gallery__track"
        onScroll={(e) => {
          const el = e.currentTarget;
          setIdx(Math.round(el.scrollLeft / el.clientWidth));
        }}
      >
        {images.map((src, i) => (
          <img
            key={i}
            className="gallery__slide"
            src={src}
            alt={`${alt} — фото ${i + 1}`}
            loading="lazy"
            onClick={() => show(i)}
          />
        ))}
      </div>
      <button className="gallery__expand" onClick={() => show(idx)} aria-label="На весь экран">
        ⤢
      </button>
      <span className="gallery__counter">
        {idx + 1} / {images.length}
      </span>
      {images.length > 1 && (
        <div className="gallery__thumbs">
          {thumbs.map((src, i) => (
            <img
              key={i}
              className={i === idx ? "gallery__thumb gallery__thumb--active" : "gallery__thumb"}
              src={src}
              alt=""
              loading="lazy"
              onClick={() => {
                const track = document.querySelector<HTMLElement>(".gallery__track");
                if (track) track.scrollTo({ left: i * track.clientWidth, behavior: "smooth" });
                setIdx(i);
              }}
            />
          ))}
          {rest > 0 && <span className="gallery__thumb gallery__thumb--more">+{rest}</span>}
        </div>
      )}

      {/* Фуллскрин-просмотр: тёмный фон, «contain» без обрезки, клик мимо — закрыть */}
      {full != null && (
        <div className="lightbox" onClick={() => setFull(null)} role="dialog" aria-modal="true">
          <img
            className="lightbox__img"
            src={images[full]}
            alt={`${alt} — фото ${full + 1}`}
            onClick={(e) => e.stopPropagation()}
          />
          <button
            className="lightbox__nav lightbox__nav--prev"
            aria-label="Предыдущее фото"
            onClick={(e) => {
              e.stopPropagation();
              show((full ?? 0) - 1);
            }}
          >
            ‹
          </button>
          <button
            className="lightbox__nav lightbox__nav--next"
            aria-label="Следующее фото"
            onClick={(e) => {
              e.stopPropagation();
              show((full ?? 0) + 1);
            }}
          >
            ›
          </button>
          <span className="lightbox__counter" onClick={(e) => e.stopPropagation()}>
            {full + 1} / {images.length}
          </span>
          <button
            className="lightbox__close"
            aria-label="Закрыть"
            onClick={() => setFull(null)}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

