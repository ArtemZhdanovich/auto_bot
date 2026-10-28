/**
 * Превью ссылок в сообщениях чата (05-chats-marketplace.md §8).
 *
 * Internal /listing/N — карточка-кнопка (фото 96px, модель, цена → PDP):
 * данные тянутся GET /api/listings/N при маунте, мини-кэш в Map —
 * повторные превью того же лота не ходят в сеть.
 *
 * External https:// — OG-превью бэка (F2): GET /api/chats/{threadId}/
 * link-preview?url=ENC → og:title/description/image карточкой-чипом с
 * домен-подписью; кэш 24ч и SSRF-фильтр — на бэке, фронт только рендерит.
 * threadId прокидывается параметром (эндпоинт живёт под тредом): ядро F1
 * (components/chat/Message) его не знает — без него внешний URL рисуется
 * плейсхолдер-чипом «🌐 домен»; null-ответ бэка → тот же плейсхолдер.
 * Партнёрский кабинет подставляет свой загрузчик (fetchPreview, papi).
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Listing } from "../api/types";
import { getRegion, priceChain } from "./fx";

/** Мини-кэш превью лотов: id → Listing (живёт жизнь модуля). */
const listingCache = new Map<number, Listing>();

// ---------- OG-превью внешних ссылок (F2, §5/§8) ----------

/** Ответ GET /api/chats/{id}/link-preview для внешней ссылки (kind=external). */
export interface LinkPreviewData {
  kind: "external";
  title: string;
  description: string;
  image: string | null;
  site_name: string;
  domain: string;
  url: string;
}

/**
 * Загрузчик OG-данных: (threadId, url) → данные | null.
 * Базовый — через JWT-клиент мини-аппа; партнёрский кабинет подставляет
 * papi-зеркало (partner/api.ts fetchPartnerLinkPreview).
 */
export type LinkPreviewFetcher = (threadId: string, url: string) => Promise<LinkPreviewData | null>;

/** Базовый загрузчик: GET /api/chats/{threadId}/link-preview?url=ENC (JWT). */
const defaultFetchPreview: LinkPreviewFetcher = (threadId, url) =>
  api<LinkPreviewData>(`/chats/${threadId}/link-preview?url=${encodeURIComponent(url)}`);

/** Мини-кэш OG-превью: `threadId|url` → данные|null (жизнь модуля). */
const ogCache = new Map<string, LinkPreviewData | null>();

/** Внутренняя ссылка на лот → его id (origin и query игнорируем). */
export function internalListingId(url: string): number | null {
  const m = /\/listing\/(\d+)/.exec(url);
  return m ? Number(m[1]) : null;
}

/** Домен ссылки для подписи чипа. */
export function linkDomain(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/**
 * Сообщение состоит из одной ссылки? (упрощение ТЗ: превью-карточка
 * рисуется НАД текстом, если всё сообщение — ровно один URL). Формы:
 * «https://…», «/listing/123», markdown-ссылка [текст](url) целиком.
 */
export function extractSoloLink(body: string): string | null {
  const t = body.trim();
  if (!t) return null;
  const md = /^\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)$/.exec(t);
  if (md) return md[2];
  if (/^https?:\/\/\S+$/i.test(t)) return t;
  if (/^\/listing\/\d+\/?$/.test(t)) return t;
  return null;
}

/** Карточка внутреннего лота: фото/модель/цена, клик → PDP. */
function ListingPreview({ id }: { id: number }) {
  const navigate = useNavigate();
  const [listing, setListing] = useState<Listing | null>(listingCache.get(id) ?? null);

  useEffect(() => {
    const cached = listingCache.get(id);
    if (cached) {
      setListing(cached);
      return;
    }
    let alive = true;
    api<Listing>(`/listings/${id}`)
      .then((l) => {
        listingCache.set(id, l);
        if (alive) setListing(l);
      })
      .catch(() => undefined); // лот недоступен — карточка останется плейсхолдером
    return () => {
      alive = false;
    };
  }, [id]);

  const price = listing
    ? priceChain(listing.price, listing.currency, getRegion())
    : null;
  const title = listing
    ? `${listing.make ?? ""} ${listing.model ?? ""}`.trim() || "Объявление"
    : "Объявление…";

  return (
    <button
      type="button"
      className="chat-link-card"
      onClick={() => navigate(`/listing/${id}`)}
    >
      {listing?.images?.[0] ? (
        <img className="chat-link-card__img" src={listing.images[0]} alt="" loading="lazy" />
      ) : (
        <span className="chat-link-card__img chat-link-card__img--empty" aria-hidden="true">
          🚗
        </span>
      )}
      <span className="chat-link-card__body">
        <span className="chat-link-card__title">
          {title}
          {listing?.year ? `, ${listing.year}` : ""}
        </span>
        <span className="chat-link-card__price">{price?.usd ?? price?.original ?? "—"}</span>
      </span>
    </button>
  );
}

/** Плейсхолдер внешней ссылки: чип «🌐 домен» (нет OG / ещё грузится). */
function LinkChip({ url }: { url: string }): JSX.Element {
  return (
    <a
      className="chat-link-chip"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
    >
      <span aria-hidden="true">🌐</span> {linkDomain(url)}
    </a>
  );
}

/** OG-карточка внешней ссылки: og:image 96px, title, description, домен. */
function ExternalPreview({
  url,
  threadId,
  fetchPreview,
}: {
  url: string;
  threadId: string;
  fetchPreview: LinkPreviewFetcher;
}): JSX.Element {
  const cacheKey = `${threadId}|${url}`;
  const [data, setData] = useState<LinkPreviewData | null | undefined>(() =>
    ogCache.has(cacheKey) ? (ogCache.get(cacheKey) ?? null) : undefined,
  );

  useEffect(() => {
    if (ogCache.has(cacheKey)) {
      setData(ogCache.get(cacheKey) ?? null);
      return;
    }
    let alive = true;
    setData(undefined); // грузим — до ответа плейсхолдер-чип (без сдвига ленты)
    fetchPreview(threadId, url)
      .then((d) => {
        ogCache.set(cacheKey, d ?? null);
        if (alive) setData(d ?? null);
      })
      .catch(() => {
        ogCache.set(cacheKey, null); // сеть/401/404 — не спамим повторами
        if (alive) setData(null);
      });
    return () => {
      alive = false;
    };
  }, [cacheKey, threadId, url, fetchPreview]);

  // нет OG-тегов (бэк отдал пустой плейсхолдер) или ошибка — чип-домен (§8)
  if (data == null) return <LinkChip url={url} />;
  return (
    <a className="chat-og" href={url} target="_blank" rel="noopener noreferrer">
      {data.image ? (
        <img className="chat-og__img" src={data.image} alt="" loading="lazy" />
      ) : (
        <span className="chat-og__img chat-og__img--empty" aria-hidden="true">
          🌐
        </span>
      )}
      <span className="chat-og__body">
        <span className="chat-og__title">{data.title || data.domain}</span>
        {data.description && <span className="chat-og__desc">{data.description}</span>}
        <span className="chat-og__domain">{data.site_name || data.domain}</span>
      </span>
    </a>
  );
}

/**
 * Превью ссылки: internal → карточка лота; external при известном треде →
 * OG-карточка бэка, иначе/при пустых тегах → плейсхолдер-чип.
 */
export function LinkPreviewCard({
  url,
  threadId,
  fetchPreview = defaultFetchPreview,
}: {
  url: string;
  /** Тред сообщения: OG-эндпоинт живёт под /api/chats/{id}/link-preview. */
  threadId?: string;
  /** Загрузчик OG (DI: партнёрский кабинет подставляет papi-версию). */
  fetchPreview?: LinkPreviewFetcher;
}): JSX.Element {
  const listingId = internalListingId(url);
  if (listingId != null) return <ListingPreview id={listingId} />;
  if (threadId != null) {
    return <ExternalPreview url={url} threadId={threadId} fetchPreview={fetchPreview} />;
  }
  return <LinkChip url={url} />;
}
