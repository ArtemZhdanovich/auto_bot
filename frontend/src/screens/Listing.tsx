/** Карточка объявления (UI/UX ТЗ §2.3): галерея, характеристики, избранное. */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Placeholder, Section, Cell } from "@telegram-apps/telegram-ui";
import { Heart, ExternalLink, Truck } from "lucide-react";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { useToast } from "../ui/Toast";
import { SkeletonCard } from "../ui/Skeleton";
import { ErrorState } from "../ui/States";
import type { Listing } from "../api/types";
import { toUsd } from "../lib/fx";
import { VERDICT_LABEL, deltaPct, type AnalysisData } from "../lib/analysis";
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

  return (
    <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "12px" }}>
      {/* Галерея: свайп + счётчик (ТЗ §2.3) */}
      {listing.images.length > 0 ? (
        <Gallery images={listing.images} alt={title} />
      ) : (
        <Placeholder description="Фото не загружены" />
      )}

      <Section header={title}>
        <Cell
          after={
            <b>
              {listing.price
                ? `${listing.price.toLocaleString("ru")} ${listing.currency ?? ""}`
                : "—"}
            </b>
          }
        >
          Цена
        </Cell>
        <Cell after={listing.year ?? "—"}>Год</Cell>
        <Cell after={listing.mileage_km ? `${listing.mileage_km.toLocaleString("ru")} км` : "—"}>
          Пробег
        </Cell>
        {usd != null && (listing.currency ?? "").toUpperCase() !== "USD" && (
          <Cell after={<b>≈ {usd.toLocaleString("ru")} $</b>}>По курсу (оценка)</Cell>
        )}
        <Cell after={listing.vin ?? "не указан"}>VIN</Cell>
        <Cell after={listing.source}>Источник</Cell>
      </Section>

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
        {analysis ? (
          <div className="analysis">
            <div
              className={`analysis__verdict analysis__verdict--${analysis.verdict}`}
            >
              {VERDICT_LABEL[analysis.verdict].emoji}{" "}
              {VERDICT_LABEL[analysis.verdict].text}
            </div>
            {analysis.fair_min != null && analysis.fair_max != null && (
              <div className="analysis__range">
                Справедливый диапазон:{" "}
                <b>
                  {Math.round(analysis.fair_min).toLocaleString("ru-RU")}–
                  {Math.round(analysis.fair_max).toLocaleString("ru-RU")}{" "}
                  {analysis.currency}
                </b>
                {deltaPct(analysis) != null && (
                  <span className="analysis__delta">
                    {" "}
                    ({deltaPct(analysis)! > 0 ? "+" : ""}
                    {deltaPct(analysis)}% от середины)
                  </span>
                )}
              </div>
            )}
            <PriceChart history={analysis.history} comps={analysis.comps} />
            {analysis.summary && <div className="analysis__summary">💬 {analysis.summary}</div>}
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
          </div>
        ) : quotaHit ? (
          <div className="analysis analysis--locked">
            🔒 Бесплатный разбор этой машины на этой неделе уже использован.
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
              loading={analyzing}
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

      <Button
        size="l"
        mode={favorited ? "filled" : "outline"}
        before={<Heart size={18} fill={favorited ? "currentColor" : "none"} />}
        onClick={() => void toggleFavorite()}
      >
        {favorited ? "В избранном" : "В избранное"}
      </Button>
      <Button
        size="l"
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
        before={<ExternalLink size={18} />}
        onClick={() => {
          haptic();
          window.open(listing.url, "_blank");
        }}
      >
        Открыть у источника
      </Button>
      <Button mode="plain" size="l" onClick={() => navigate(-1)}>
        Назад
      </Button>
    </div>
  );
}

function Gallery({ images, alt }: { images: string[]; alt: string }) {
  const [idx, setIdx] = useState(0);
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
          <img key={i} className="gallery__slide" src={src} alt={`${alt} — фото ${i + 1}`} loading="lazy" />
        ))}
      </div>
      <span className="gallery__counter">
        {idx + 1} / {images.length}
      </span>
    </div>
  );
}

