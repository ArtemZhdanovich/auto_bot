/** Карточка объявления (UI/UX ТЗ §2.3): галерея, характеристики, избранное. */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Placeholder, Section, Cell } from "@telegram-apps/telegram-ui";
import { Heart, ExternalLink, Truck } from "lucide-react";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { useToast } from "../ui/Toast";
import { SkeletonCard } from "../ui/Skeleton";
import { ErrorState } from "../ui/States";
import type { Listing } from "../api/types";
import { getRegion, priceChain, toUsd } from "../lib/fx";
import { VERDICT_LABEL, deltaPct, fairPosition, type AnalysisData } from "../lib/analysis";
import { PriceChart } from "../ui/PriceChart";

export function ListingScreen() {
  const { id } = useParams<{ id: string }>();
  const [listing, setListing] = useState<Listing | null>(null);
  const [favorited, setFavorited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisData | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [quotaHit, setQuotaHit] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    api<Listing>(`/listings/${id}`)
      .then(setListing)
      .catch((e: Error) => setError(e.message));
    api<{ favorited: boolean }>(`/favorites/${id}`)
      .then((r) => setFavorited(r.favorited))
      .catch(() => undefined); // вне Telegram — без избранного
    // Кэш последнего разбора (без LLM): если есть — сразу график
    api<AnalysisData | null>(`/listings/${id}/analysis`)
      .then((a) => a && setAnalysis(a))
      .catch(() => undefined);
  }, [id]);

  const runAnalysis = async () => {
    if (analyzing) return;
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
      <div className="listing-page__main">
        {/* Галерея: свайп + счётчик + фуллскрин по клику (ТЗ §2.3) */}
        {listing.images.length > 0 ? (
          <Gallery images={listing.images} alt={title} />
        ) : (
          <Placeholder description="Фото не загружены" />
        )}

        <Section header={title}>
          <Cell
            after={
              <b>
                {chain.usd
                  ? `${chain.usd}${chain.local ? ` · ${chain.local}` : ""}`
                  : chain.original ?? "—"}
              </b>
            }
          >
            Цена
          </Cell>
          {chain.original && (
            <Cell after={<b>{chain.original}</b>}>Цена у источника</Cell>
          )}
          <Cell after={listing.year ?? "—"}>Год</Cell>
          <Cell after={listing.mileage_km ? `${listing.mileage_km.toLocaleString("ru")} км` : "—"}>
            Пробег
          </Cell>
          {usd != null && (listing.currency ?? "").toUpperCase() !== "USD" && (
            <Cell after={<b>≈ {usd.toLocaleString("ru")} $</b>}>По курсу (оценка)</Cell>
          )}
          <Cell after={listing.vin ?? "не указан"}>VIN</Cell>
          <Cell after={listing.source}>Источник</Cell>
          <Cell after={new Date(listing.parsed_at).toLocaleDateString("ru")}>Обновлено</Cell>
        </Section>

      </div>

      <div className="listing-page__side">
      {/* Название с источника (PLAN.md, фаза 5): перевод, иначе оригинал */}
      {(listing.title_ru ?? listing.title_raw) && (
        <Section>
          <Cell after={<b>{listing.title_ru ?? listing.title_raw}</b>}>
            Название
          </Cell>
        </Section>
      )}

      {/* AI-анализ стоимости (монетизация: 1/нед бесплатно, далее Pro) */}
      <Section header="🤖 AI-анализ цены">
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
            <Button
              size="s"
              mode="filled"
              stretched
              style={{ marginTop: 10 }}
              onClick={() => {
                haptic();
                navigate("/profile");
              }}
            >
              ⚡ Безлимит в Pro
            </Button>
          </div>
        ) : (
          <div className="analysis">
            <div className="analysis__hint">
              Нейросеть сравнит цену с рынком: сопоставимые объявления, история
              цены, вердикт и факторы.
            </div>
            <Button
              size="s"
              mode="filled"
              stretched
              onClick={() => void runAnalysis()}
            >
              🤖 Анализировать (1 бесплатно в неделю)
            </Button>
          </div>
        )}
      </Section>

      {/* История цен (PLAN.md, фаза 4): точки listing_price_history, новые сверху */}
      {listing.price_history && listing.price_history.length > 1 && (
        <Section header="История цен">
          {[...listing.price_history].reverse().map((p, i) => (
            <Cell
              key={`${p.recorded_at}-${i}`}
              after={
                <b>
                  {p.price.toLocaleString("ru")} {p.currency ?? ""}
                </b>
              }
            >
              {new Date(p.recorded_at).toLocaleDateString("ru")}
              {i === 0 ? " · текущая" : ""}
            </Cell>
          ))}
        </Section>
      )}
      {listing.is_active === false && (
        <Placeholder
          header="🔴 Исчезло с продажи"
          description="Объявления больше нет на источнике"
        />
      )}

      {/* CTA-блок: primary один — «Открыть у источника» (e-commerce стандарт) */}
      <Button
        size="l"
        mode="filled"
        before={<ExternalLink size={18} />}
        onClick={() => {
          haptic();
          window.open(listing.url, "_blank");
        }}
      >
        Открыть у источника
      </Button>
      <Button
        size="l"
        mode="outline"
        before={<Truck size={18} />}
        onClick={() => {
          haptic();
          navigate(
            `/calc?${usd != null ? `price=${usd}&` : ""}year=${listing.year ?? ""}`,
          );
        }}
      >
        Доставка и растаможка
      </Button>
      <Button
        size="l"
        mode="outline"
        before={
          <Heart
            size={18}
            fill={favorited ? "var(--accent)" : "none"}
            color={favorited ? "var(--accent)" : undefined}
          />
        }
        onClick={() => void toggleFavorite()}
      >
        {favorited ? "В избранном" : "В избранное"}
      </Button>
      <Button mode="plain" size="l" onClick={() => navigate(-1)}>
        Назад
      </Button>
      </div>

      {/* Sticky-CTA на мобиле: цена + главное действие всегда в зоне пальца */}
      <div className="listing-cta-bar">
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

