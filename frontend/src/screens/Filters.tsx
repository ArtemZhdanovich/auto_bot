/**
 * «Сигналы» — уведомления о новых лотах (01-ux-audit-ia.md §1.5, S1–S6;
 * тексты — 03 §2.6). Прежнее название экрана — «Фильтры».
 *
 *  S1 — список сигналов сверху карточками, создание по кнопке
 *       «+ Новый сигнал» (форма больше не занимает верх экрана);
 *  S3 — счётчик «Сигналы · N/M» из GET /subscription (max_filters);
 *       режим «сразу при публикации» с замочком вне Business;
 *  S4 — удаление с подтверждением: «Удалить?» → «Точно удалить / Отмена»;
 *  S5 — редактирование по тапу по карточке (PATCH /filters/{id});
 *  S6 — дублей списка в «Анализах» больше нет (там блок удалён).
 *
 * Форма — inline-панель на десктопе, bottom-sheet на мобиле (CSS в
 * screens-extra.css). Inline-валидация цена/год/пробег — тексты 03 §3.2.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import type { Criteria, Subscription, UserFilter } from "../api/types";
import {
  BODIES,
  DRIVES,
  FUELS,
  TRANSMISSIONS,
} from "../lib/specs";
import { useToast } from "../ui/Toast";
import { apiErrorText, apiErrorView, VALIDATION_TEXTS } from "./apiErrorText";

interface FormState {
  name: string;
  make: string;
  model: string;
  yearFrom: string;
  yearTo: string;
  priceMin: string;
  priceMax: string;
  mileageMax: string;
  fuel: string;
  transmission: string;
  bodyType: string;
  drive: string;
}

const EMPTY: FormState = {
  name: "",
  make: "",
  model: "",
  yearFrom: "",
  yearTo: "",
  priceMin: "",
  priceMax: "",
  mileageMax: "",
  fuel: "",
  transmission: "",
  bodyType: "",
  drive: "",
};

const onlyDigits = (s: string) => s.replace(/\D/g, "").slice(0, 7);

function criteriaOf(f: FormState) {
  const num = (s: string) => (s ? Number(s) : undefined);
  return {
    ...(f.make ? { make: f.make } : {}),
    ...(f.model ? { model: f.model } : {}),
    ...(f.yearFrom ? { year_from: num(f.yearFrom) } : {}),
    ...(f.yearTo ? { year_to: num(f.yearTo) } : {}),
    ...(f.priceMin ? { price_min: num(f.priceMin) } : {}),
    ...(f.priceMax ? { price_max: num(f.priceMax) } : {}),
    ...(f.mileageMax ? { mileage_max: num(f.mileageMax) } : {}),
    ...(f.fuel ? { fuel: f.fuel } : {}),
    ...(f.transmission ? { transmission: f.transmission } : {}),
    ...(f.bodyType ? { body_type: f.bodyType } : {}),
    ...(f.drive ? { drive: f.drive } : {}),
  };
}

const CRITERIA_LABELS: Record<string, string> = {
  year_from: "от",
  year_to: "до",
  price_min: "от $",
  price_max: "до $",
  mileage_max: "до км",
  fuel: "топливо",
  transmission: "КПП",
  body_type: "кузов",
  drive: "привод",
};

/** Критерии человекочитаемо: «Palisade, 2022–2024, до $45 000». */
function describe(criteria: Criteria): string {
  const parts: string[] = [];
  if (criteria.make) parts.push(criteria.make);
  if (criteria.model) parts.push(criteria.model);
  for (const [key, label] of Object.entries(CRITERIA_LABELS)) {
    const v = (criteria as Record<string, unknown>)[key];
    if (v != null) parts.push(`${label} ${v}`);
  }
  return parts.join(", ") || "все объявления";
}

/** Режим доставки человекочитаемо (S3): digest зависит от тарифа. */
function modeLabel(mode: UserFilter["notify_mode"], sub: Subscription | null): string {
  if (mode === "instant") return "сразу при публикации · Business";
  const cadence = sub?.digest === "instant" || sub?.digest === "daily" ? "ежедневно" : "раз в неделю по тарифу";
  return `дайджест: ${cadence}`;
}

/** Inline-валидация формы (03 §3.2): цена/год/пробег. */
interface FieldErrors {
  priceRange?: string;
  yearRange?: string;
  yearBounds?: string;
  mileage?: string;
}

function validate(f: FormState): FieldErrors {
  const errs: FieldErrors = {};
  const nextYear = new Date().getFullYear() + 1;
  const yFrom = f.yearFrom ? Number(f.yearFrom) : null;
  const yTo = f.yearTo ? Number(f.yearTo) : null;
  if (yFrom != null && yTo != null && yFrom > yTo) {
    errs.yearRange = VALIDATION_TEXTS.yearRange;
  } else {
    if (yFrom != null && (yFrom < 1980 || yFrom > nextYear)) errs.yearBounds = VALIDATION_TEXTS.yearBounds;
    if (yTo != null && (yTo < 1980 || yTo > nextYear)) errs.yearBounds = VALIDATION_TEXTS.yearBounds;
  }
  if (f.priceMin && f.priceMax && Number(f.priceMin) > Number(f.priceMax)) {
    errs.priceRange = VALIDATION_TEXTS.priceRange;
  }
  if (f.mileageMax === "0") {
    errs.mileage = VALIDATION_TEXTS.mileage;
  }
  return errs;
}

export function FiltersScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const [filters, setFilters] = useState<UserFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [sub, setSub] = useState<Subscription | null>(null);
  /** Редактор: null — закрыт; id — редактирование (S5); 0 — создание. */
  const [editorId, setEditorId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [instant, setInstant] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<{ text: string; cta?: { label: string; to: string } } | null>(null);
  /** S4: id сигнала в состоянии подтверждения удаления. */
  const [deleteId, setDeleteId] = useState<number | null>(null);
  /** Апселл лимита Free: пока пользователь не закрыл подсказку. */
  const [upsellHidden, setUpsellHidden] = useState(false);

  const set = (k: keyof FormState) => (v: string) =>
    setForm((p) => ({ ...p, [k]: v }));

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setFilters(await api<UserFilter[]>("/filters"));
    } catch (e) {
      setLoadError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    // Тариф — для счётчика N/M, каденса дайджеста и замка instant (S3);
    // вне Telegram или для гостя может не прийти — экран работает и без него
    api<Subscription>("/subscription")
      .then(setSub)
      .catch(() => undefined);
  }, [reload]);

  const instantAllowed = sub?.instant ?? false;
  const maxFilters = sub?.max_filters ?? null;
  const atLimit =
    maxFilters != null && filters.length >= maxFilters && sub?.tier === "free";

  /** Открыть редактор: 0 = создание, id = правка с префиллом (S5). */
  const openEditor = (f: UserFilter | null) => {
    setFormError(null);
    setFieldErrors({});
    setInstant(false);
    if (!f) {
      setForm(EMPTY);
      setEditorId(0);
      return;
    }
    const c = f.criteria as Record<string, unknown>;
    setForm({
      name: f.name ?? "",
      make: (c.make as string) ?? "",
      model: (c.model as string) ?? "",
      yearFrom: c.year_from != null ? String(c.year_from) : "",
      yearTo: c.year_to != null ? String(c.year_to) : "",
      priceMin: c.price_min != null ? String(c.price_min) : "",
      priceMax: c.price_max != null ? String(c.price_max) : "",
      mileageMax: c.mileage_max != null ? String(c.mileage_max) : "",
      fuel: (c.fuel as string) ?? "",
      transmission: (c.transmission as string) ?? "",
      bodyType: (c.body_type as string) ?? "",
      drive: (c.drive as string) ?? "",
    });
    setInstant(f.notify_mode === "instant");
    setEditorId(f.id);
  };

  const save = async () => {
    if (saving) return;
    const criteria = criteriaOf(form);
    if (Object.keys(criteria).length === 0) {
      setFormError({ text: VALIDATION_TEXTS.emptyForm });
      return;
    }
    const errs = validate(form);
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) return;

    setSaving(true);
    setFormError(null);
    const body = JSON.stringify({
      name: form.name || null,
      criteria,
      notify_mode: instant ? "instant" : "digest",
    });
    try {
      if (editorId === 0) {
        await api<UserFilter>("/filters", { method: "POST", body });
        haptic();
        toast.show("Уведомление создано — напишем в Telegram");
      } else if (editorId != null) {
        await api<UserFilter>(`/filters/${editorId}`, { method: "PATCH", body });
        haptic();
        toast.show("Сигнал обновлён");
      }
      setEditorId(null);
      await reload();
    } catch (e) {
      // 403-лимит и прочее — по маппингу 03 §3.2 (сырой detail не показываем)
      const view = apiErrorView(e);
      setFormError({
        text: view.text,
        cta: view.action ? { label: view.action.label, to: view.action.to ?? "/profile" } : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: number) => {
    try {
      await api<void>(`/filters/${id}`, { method: "DELETE" });
      haptic();
      toast.show("Уведомление удалено");
      setDeleteId(null);
      if (editorId === id) setEditorId(null);
      await reload();
    } catch (e) {
      toast.show(apiErrorText(e), { error: true });
      setDeleteId(null);
    }
  };

  const renderEditor = () => (
    <section className="sig-editor" aria-label={editorId === 0 ? "Новый сигнал" : "Редактирование сигнала"}>
      <div className="sig-editor__head">
        <h3 style={{ margin: 0 }}>{editorId === 0 ? "Новый сигнал" : "Редактирование"}</h3>
        <button type="button" className="ai-chat__reset" aria-label="Закрыть редактор" onClick={() => setEditorId(null)}>
          ✕
        </button>
      </div>
      <p className="sig-editor__hint">
        Заполните, какой авто ищете, — и мы напишем вам в Telegram, когда такой
        появится в продаже.
      </p>
      <div className="filters-form">
        <label className="calc-field">
          <span>Название (необязательно)</span>
          <input value={form.name} onChange={(e) => set("name")(e.target.value)} placeholder="Семейный кроссовер" />
        </label>
        <div className="filter-pair">
          <label className="calc-field">
            <span>Марка</span>
            <input value={form.make} onChange={(e) => set("make")(e.target.value)} placeholder="BMW" />
          </label>
          <label className="calc-field">
            <span>Модель</span>
            <input value={form.model} onChange={(e) => set("model")(e.target.value)} placeholder="X5" />
          </label>
        </div>
        <div className="filter-pair">
          <label className="calc-field">
            <span>Год от</span>
            <input inputMode="numeric" value={form.yearFrom} onChange={(e) => set("yearFrom")(onlyDigits(e.target.value))} placeholder="2019" />
            <span className="sig-field-error" role="alert">{fieldErrors.yearRange ?? fieldErrors.yearBounds ?? ""}</span>
          </label>
          <label className="calc-field">
            <span>Год до</span>
            <input inputMode="numeric" value={form.yearTo} onChange={(e) => set("yearTo")(onlyDigits(e.target.value))} placeholder="2024" />
            <span className="sig-field-error" />
          </label>
        </div>
        <div className="filter-pair">
          <label className="calc-field">
            <span>Цена $ от</span>
            <input inputMode="numeric" value={form.priceMin} onChange={(e) => set("priceMin")(onlyDigits(e.target.value))} placeholder="15000" />
            <span className="sig-field-error" role="alert">{fieldErrors.priceRange ?? ""}</span>
          </label>
          <label className="calc-field">
            <span>Цена $ до</span>
            <input inputMode="numeric" value={form.priceMax} onChange={(e) => set("priceMax")(onlyDigits(e.target.value))} placeholder="40000" />
            <span className="sig-field-error" />
          </label>
        </div>
        <label className="calc-field">
          <span>Пробег, км — не более</span>
          <input inputMode="numeric" value={form.mileageMax} onChange={(e) => set("mileageMax")(onlyDigits(e.target.value))} placeholder="100000" />
          <span className="sig-field-error" role="alert">{fieldErrors.mileage ?? ""}</span>
        </label>
        <div className="filter-pair">
          <label className="filter-select">
            <span>Топливо</span>
            <select value={form.fuel} onChange={(e) => set("fuel")(e.target.value)}>
              {FUELS.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </label>
          <label className="filter-select">
            <span>КПП</span>
            <select value={form.transmission} onChange={(e) => set("transmission")(e.target.value)}>
              {TRANSMISSIONS.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="filter-pair">
          <label className="filter-select">
            <span>Кузов</span>
            <select value={form.bodyType} onChange={(e) => set("bodyType")(e.target.value)}>
              {BODIES.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </label>
          <label className="filter-select">
            <span>Привод</span>
            <select value={form.drive} onChange={(e) => set("drive")(e.target.value)}>
              {DRIVES.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </label>
        </div>

        {/* S3: режим доставки — «подборка» или «сразу» (Business, замок) */}
        <div className="sig-mode" role="group" aria-label="Режим доставки">
          <button
            type="button"
            className="sig-mode__opt"
            aria-pressed={!instant}
            onClick={() => setInstant(false)}
          >
            📋 Подборка раз в день
          </button>
          <button
            type="button"
            className={instantAllowed ? "sig-mode__opt" : "sig-mode__opt sig-mode__opt--locked"}
            aria-pressed={instant}
            aria-describedby="sig-mode-hint"
            onClick={() => {
              if (instantAllowed) {
                setInstant(true);
              } else {
                // Обещаем невозможное честно: тарифный гейт сразу, а не в 403
                setFormError({
                  text: "«Сразу при публикации» — тариф Business. На Pro подборка приходит дайджестом раз в день.",
                  cta: { label: "Смотреть тарифы", to: "/profile" },
                });
              }
            }}
          >
            ⚡ Сразу{instantAllowed ? "" : " — Business"}
          </button>
        </div>
        <div className="sig-mode__hint" id="sig-mode-hint">
          {instant
            ? "Сообщим сразу, как только авто появится"
            : "Иначе пришлём подборкой раз в день"}
        </div>

        {formError && (
          <div className="form-error-inline" role="alert">
            <span>{formError.text}</span>
            {formError.cta && (
              <button type="button" className="btn" onClick={() => navigate(formError.cta!.to)}>
                {formError.cta.label}
              </button>
            )}
          </div>
        )}

        <div className="sig-editor__actions">
          <button className="btn btn--primary" disabled={saving} onClick={() => void save()}>
            {saving
              ? editorId === 0 ? "Создаём…" : "Сохраняем…"
              : editorId === 0 ? "Создать уведомление" : "Сохранить сигнал"}
          </button>
          <button className="btn" onClick={() => setEditorId(null)}>
            Отмена
          </button>
        </div>
      </div>
    </section>
  );

  return (
    <div className="screen">
      <div className="sig-head">
        <h2 className="screen-title">Сигналы</h2>
        {maxFilters != null && (
          <span className="sig-count tabular">
            {filters.length}/{maxFilters}
          </span>
        )}
      </div>

      {/* S3: лимит Free исчерпан — апселл (тексты 03 §2.6) */}
      {atLimit && !upsellHidden && editorId === null && (
        <div className="sig-upsell" role="note">
          <div className="sig-upsell__title">Лимит уведомлений тарифа Free — {maxFilters}</div>
          <p className="sig-upsell__text">
            Удалите ненужное или оформите Pro: до 10 уведомлений и ежедневный
            дайджест.
          </p>
          <div className="sig-upsell__actions">
            <button className="btn" onClick={() => navigate("/profile")}>
              Смотреть тарифы
            </button>
            <button className="btn" onClick={() => setUpsellHidden(true)}>
              Удалить лишнее
            </button>
          </div>
        </div>
      )}

      {loading && (
        <>
          <div className="skeleton sig-skeleton" />
          <div className="skeleton sig-skeleton" />
          <div className="skeleton sig-skeleton" />
        </>
      )}

      {!loading && loadError != null && (
        <div className="state state--page">
          <div className="state__emoji">⚠️</div>
          <div className="state__title">Не удалось загрузить</div>
          <div className="state__text">{apiErrorText(loadError)}</div>
          <button className="btn" onClick={() => void reload()}>
            Повторить
          </button>
        </div>
      )}

      {!loading && loadError == null && filters.length === 0 && editorId === null && (
        <div className="state state--page">
          <div className="state__emoji">🔔</div>
          <div className="state__title">Уведомлений пока нет</div>
          <div className="state__text">
            Создайте первое — и мы напишем, когда появится подходящий авто
          </div>
          <button className="btn btn--primary" onClick={() => openEditor(null)}>
            Создать
          </button>
        </div>
      )}

      {editorId !== null && renderEditor()}

      {!loading && loadError == null && editorId === null && filters.length > 0 && (
        <div className="sig-list">
          {filters.map((f) => {
            const name = f.name ?? `Сигнал #${f.id}`;
            const confirming = deleteId === f.id;
            return confirming ? (
              // S4: подтверждение вместо мгновенного удаления
              <div key={f.id} className="sig-del-confirm">
                <div className="sig-del-confirm__text">
                  Удалить уведомление «{name}»? Новые лоты по нему больше не придут.
                </div>
                <div className="sig-del-confirm__actions">
                  <button className="btn btn--danger" onClick={() => void remove(f.id)}>
                    Точно удалить
                  </button>
                  <button className="btn" onClick={() => setDeleteId(null)}>
                    Отмена
                  </button>
                </div>
              </div>
            ) : (
              <div
                key={f.id}
                className="sig-card"
                role="button"
                tabIndex={0}
                aria-label={`Редактировать сигнал ${name}`}
                onClick={() => openEditor(f)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openEditor(f);
                  }
                }}
              >
                <div className="sig-card__body">
                  <div className="sig-card__name">{name}</div>
                  <div className="sig-card__desc">{describe(f.criteria)}</div>
                  <span
                    className={
                      f.notify_mode === "instant"
                        ? "sig-card__mode sig-card__mode--instant"
                        : "sig-card__mode"
                    }
                  >
                    {f.notify_mode === "instant" ? "⚡" : "📋"} {modeLabel(f.notify_mode, sub)}
                  </span>
                </div>
                <button
                  type="button"
                  className="sig-card__del"
                  aria-label={`Удалить ${name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setDeleteId(f.id);
                  }}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}

      {!loading && loadError == null && editorId === null && (filters.length > 0 || atLimit) && (
        <button type="button" className="sig-add" onClick={() => openEditor(null)}>
          + Новый сигнал
        </button>
      )}
    </div>
  );
}
