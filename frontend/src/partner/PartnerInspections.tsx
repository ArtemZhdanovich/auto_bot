/**
 * «Чеки и история» (docs/REDESIGN/07-sell-chats-partner-inspection.md §3, F2):
 * конструктор чека осмотра «как в Корее» для своих лотов + таймлайн
 * истории машины.
 *
 * Два уровня:
 * (а) список своих лотов (reuse GET /api/partner/market/lots) с индикатором
 *     «чек есть/нет» — inspections дёргаются параллельно для видимых лотов;
 * (б) выбор лота → редактор чека:
 *     - диаграммы Encar как в components/Inspection.tsx (01.gif внешние
 *       панели + 02_new.gif каркас); клики по зонам — через мини-карту
 *       координат ZONE_POS, скопированную с бэка (_ZONE_POS в
 *       app/infrastructure/sources/encar.py): px канвы 254x282 → %.
 *       Общий модуль с мини-аппом не заводим (Inspection.tsx не трогаем),
 *       словари зон/типов/чек-листа — константы в partner/api.ts;
 *     - чек-лист агрегатов: строка × сегмент статуса + заметка;
 *     - шапка: центр осмотра / дата / оценка / комментарий / фото-ссылки;
 *     - история машины: события {date, title, note} ≤ 50 → PUT history
 *       (чтение — публичная карточка ListingOut.owner_history).
 *
 * Мини-апп (components/**, lib/**) не изменяется и не импортируется —
 * партнёрские файлы самодостаточны.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  INSPECTION_BODY_ZONES,
  INSPECTION_GRADES,
  INSPECTION_ITEM_KEYS,
  INSPECTION_ITEM_STATUSES,
  INSPECTION_MARK_TYPES,
  PartnerApiError,
  fetchListingOwnerHistory,
  fetchPartnerInspection,
  fetchPartnerLots,
  partnerFail,
  putPartnerHistory,
  putPartnerInspection,
  type PartnerBodyZone,
  type PartnerGrade,
  type PartnerHistoryEvent,
  type PartnerInspectionMark,
  type PartnerInspectionReport,
  type PartnerItemKey,
  type PartnerItemStatus,
  type PartnerMarkType,
} from "./api";
import type { MyLot } from "../api/types";
import { EmptyBlock, ErrorBlock, Skel, SkelRows, fmtMoneyCompact } from "./components/bits";
import { usePToast } from "./components/toast";

// ---------- Словари осмотра (копия значений lib/inspection.ts мини-аппа) ----------

/** Русские названия зон (BODY_ZONES). */
const ZONE_LABEL: Record<PartnerBodyZone, string> = {
  front_bumper: "Передний бампер",
  hood: "Капот",
  roof: "Крыша",
  trunk: "Крышка багажника",
  fender_fl: "Крыло переднее, левое",
  fender_fr: "Крыло переднее, правое",
  fender_rl: "Крыло заднее, левое",
  fender_rr: "Крыло заднее, правое",
  door_fl: "Дверь передняя, левая",
  door_fr: "Дверь передняя, правая",
  door_rl: "Дверь задняя, левая",
  door_rr: "Дверь задняя, правая",
  rocker_l: "Порог, левый",
  rocker_r: "Порог, правый",
  frame_front: "Передняя панель / лонжероны",
  frame_inside_l: "Брызговик (апрон), левый",
  frame_inside_r: "Брызговик (апрон), правый",
  wheelhouse_fl: "Стакан переднего колеса, левый",
  wheelhouse_fr: "Стакан переднего колеса, правый",
  wheelhouse_rl: "Арка заднего колеса, левая",
  wheelhouse_rr: "Арка заднего колеса, правая",
  frame_floor: "Пол кузова",
  frame_rear: "Задняя панель / пол багажника",
  pillar_a_l: "Стойка A, левая",
  pillar_b_l: "Стойка B, левая",
  pillar_c_l: "Стойка C, левая",
  pillar_a_r: "Стойка A, правая",
  pillar_b_r: "Стойка B, правая",
  pillar_c_r: "Стойка C, правая",
};

/** Типы повреждений (MARK_TYPES): подпись и цвет пина на диаграмме. */
const MARK_LABEL: Record<PartnerMarkType, string> = {
  scratch: "Царапина",
  dent: "Вмятина",
  repaint: "Покраска",
  replace: "Замена элемента",
  rust: "Коррозия",
  crack: "Трещина",
  glass: "Повреждение стекла/оптики",
  repair: "Ремонт / окраска панели",
  frame: "Дефект силовой структуры",
};

const MARK_COLOR: Record<PartnerMarkType, string> = {
  scratch: "#f59e0b",
  dent: "#ef4444",
  repaint: "#8b5cf6",
  replace: "#111827",
  rust: "#b45309",
  crack: "#dc2626",
  glass: "#06b6d4",
  repair: "#f97316",
  frame: "#991b1b",
};

/** Порядок важности: чем левее, тем сильнее дефект — им красится пин зоны. */
const MARK_SEVERITY: PartnerMarkType[] = [
  "replace", "frame", "crack", "dent", "rust", "repair", "repaint", "glass", "scratch",
];

const ITEM_LABEL: Record<PartnerItemKey, string> = {
  engine: "Двигатель",
  transmission: "Трансмиссия / КПП",
  suspension: "Подвеска",
  brakes: "Тормозная система",
  steering: "Рулевое управление",
  electronics: "Электроника / приборы",
  interior: "Салон",
  optics: "Оптика",
  glass: "Стёкла",
  tires: "Шины / диски",
  ac: "Кондиционер / климат",
  leaks: "Потёки технических жидкостей",
};

const STATUS_LABEL: Record<PartnerItemStatus, string> = {
  ok: "Ок",
  warn: "Внимание",
  defect: "Дефект",
  na: "Н. д.",
};

const GRADE_LABEL: Record<PartnerGrade, string> = {
  excellent: "Отличное",
  good: "Хорошее",
  fair: "Удовлетворительное",
  poor: "Требует внимания",
};

// ---------- Диаграммы Encar и мини-карта зон ----------

/** Официальные диаграммы чека Encar: вид сверху, 254x282, перед вверху. */
const DIAGRAM_BASE = "https://www.encar.com/images/pop/check";
const DIAGRAM_W = 254;
const DIAGRAM_H = 282;

type DiagramKey = "outer" | "frame";

const DIAGRAMS: { key: DiagramKey; title: string; img: string }[] = [
  { key: "outer", title: "Кузов — внешние панели", img: `${DIAGRAM_BASE}/img_inspection01.gif` },
  { key: "frame", title: "Силовая структура", img: `${DIAGRAM_BASE}/img_inspection02_new.gif` },
];

/**
 * Мини-карта координат зон на диаграммах (px 254x282) — копия _ZONE_POS
 * бэкенда (app/infrastructure/sources/encar.py): «outer» — координаты
 * 01.gif, «frame» — 02_new.gif (после сдвига x-340 общей канвы
 * классической страницы чека). front_bumper на картах Encar места не
 * имеет (бампер отмечают по frame_front) — зоны без координат
 * некликабельны, как в оригинальном чеке.
 */
const ZONE_POS: Partial<Record<PartnerBodyZone, { d: DiagramKey; x: number; y: number }>> = {
  // Внешние панели (01.gif)
  hood: { d: "outer", x: 120, y: 47 },
  fender_fl: { d: "outer", x: 33, y: 66 },
  fender_fr: { d: "outer", x: 202, y: 66 },
  door_fl: { d: "outer", x: 33, y: 115 },
  door_fr: { d: "outer", x: 202, y: 115 },
  door_rl: { d: "outer", x: 33, y: 165 },
  door_rr: { d: "outer", x: 202, y: 165 },
  rocker_l: { d: "outer", x: 4, y: 140 },
  rocker_r: { d: "outer", x: 233, y: 140 },
  fender_rl: { d: "outer", x: 37, y: 211 },
  fender_rr: { d: "outer", x: 199, y: 211 },
  trunk: { d: "outer", x: 120, y: 235 },
  roof: { d: "outer", x: 120, y: 170 },
  // Силовая структура (02_new.gif)
  frame_front: { d: "frame", x: 174, y: 33 },
  frame_inside_l: { d: "frame", x: 140, y: 56 },
  frame_inside_r: { d: "frame", x: 205, y: 56 },
  wheelhouse_fl: { d: "frame", x: 140, y: 77 },
  wheelhouse_fr: { d: "frame", x: 205, y: 77 },
  pillar_a_l: { d: "frame", x: 115, y: 112 },
  pillar_a_r: { d: "frame", x: 233, y: 112 },
  pillar_b_l: { d: "frame", x: 110, y: 142 },
  pillar_b_r: { d: "frame", x: 235, y: 142 },
  pillar_c_l: { d: "frame", x: 118, y: 178 },
  pillar_c_r: { d: "frame", x: 229, y: 178 },
  frame_floor: { d: "frame", x: 174, y: 145 },
  wheelhouse_rl: { d: "frame", x: 138, y: 211 },
  wheelhouse_rr: { d: "frame", x: 210, y: 211 },
  frame_rear: { d: "frame", x: 174, y: 217 },
};

/** Зоны конкретной диаграммы (для отрисовки кликабельных областей). */
const zonesOf = (d: DiagramKey): PartnerBodyZone[] =>
  INSPECTION_BODY_ZONES.filter((z) => ZONE_POS[z]?.d === d);

/** Сегодня YYYY-MM-DD — дефолт даты события истории. */
function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Состояние чек-листа редактора: агрегат → статус + заметка. */
type ItemState = Record<PartnerItemKey, { status: PartnerItemStatus; note: string }>;

function emptyItems(): ItemState {
  const state = {} as ItemState;
  for (const k of INSPECTION_ITEM_KEYS) state[k] = { status: "na", note: "" };
  return state;
}

// ============================================================
// Список лотов (уровень «а»)
// ============================================================

export function PartnerInspections({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [lots, setLots] = useState<MyLot[] | null>(null);
  const [lotsErr, setLotsErr] = useState<string | null>(null);
  /** listing_id → чек есть? (undefined — ещё неизвестно, показываем скелет). */
  const [hasInsp, setHasInsp] = useState<Record<number, boolean | undefined>>({});
  const [selected, setSelected] = useState<MyLot | null>(null);

  const load = useCallback(() => {
    setLots(null);
    setLotsErr(null);
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

  /** Индикаторы «чек есть/нет»: inspections для видимых лотов параллельно. */
  useEffect(() => {
    if (!lots) return;
    let alive = true;
    for (const lot of lots) {
      fetchPartnerInspection(lot.listing_id)
        .then((report) => {
          if (alive) setHasInsp((m) => ({ ...m, [lot.listing_id]: report != null }));
        })
        .catch(() => undefined); // флаг останется «неизвестно» — список не блокируем
    }
    return () => {
      alive = false;
    };
  }, [lots]);

  /** Редактор сохранил чек — индикатор лота станет зелёным без перечитки. */
  const onInspectionSaved = useCallback((listingId: number) => {
    setHasInsp((m) => ({ ...m, [listingId]: true }));
  }, []);

  if (selected) {
    return (
      <InspectionEditor
        lot={selected}
        onUnauthorized={onUnauthorized}
        onBack={() => setSelected(null)}
        onInspectionSaved={onInspectionSaved}
      />
    );
  }

  return (
    <>
      <h1 className="pa-title">Чеки и история</h1>
      <p className="pa-footnote">
        Чек осмотра «как в Корее»: диаграмма повреждений, чек-лист агрегатов и
        история машины — всё видно покупателям в карточке лота.
      </p>

      {lotsErr ? (
        <ErrorBlock description={lotsErr} onAction={load} />
      ) : lots === null ? (
        <SkelRows n={3} />
      ) : lots.length === 0 ? (
        <EmptyBlock
          title="Своих лотов нет"
          description="Разместите лот в разделе «Объявления» — здесь появится конструктор чека осмотра."
        />
      ) : (
        <div className="pa-insp-lots">
          {lots.map((lot) => {
            const name = `${lot.make ?? ""} ${lot.model ?? ""}`.trim() || `Лот #${lot.listing_id}`;
            const flag = hasInsp[lot.listing_id];
            return (
              <article key={lot.listing_id} className="pa-insp-lot">
                {lot.images?.[0] ? (
                  <img className="pa-insp-lot__img" src={lot.images[0]} alt="" loading="lazy" />
                ) : (
                  <span className="pa-insp-lot__img pa-insp-lot__img--empty" aria-hidden="true">
                    🚗
                  </span>
                )}
                <div className="pa-insp-lot__main">
                  <span className="pa-insp-lot__name">
                    {name}
                    {lot.year ? `, ${lot.year}` : ""}
                  </span>
                  <span className="pa-insp-lot__meta">
                    #{lot.listing_id}
                    {lot.price != null && ` · ${fmtMoneyCompact(lot.price, lot.currency)}`}
                  </span>
                  {flag === undefined ? (
                    <Skel w={72} h={18} />
                  ) : (
                    <span
                      className={
                        flag ? "pa-insp-check pa-insp-check--ok" : "pa-insp-check pa-insp-check--none"
                      }
                    >
                      <i aria-hidden="true">{flag ? "✓" : "＋"}</i>
                      {flag ? "Чек есть" : "Чека нет"}
                    </span>
                  )}
                </div>
                <div className="pa-insp-lot__actions">
                  <button
                    type="button"
                    className="btn btn--primary pa-btn-sm"
                    onClick={() => setSelected(lot)}
                  >
                    Открыть чек
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}

// ============================================================
// Редактор чека + история (уровень «б»)
// ============================================================

function InspectionEditor({
  lot,
  onUnauthorized,
  onBack,
  onInspectionSaved,
}: {
  lot: MyLot;
  onUnauthorized: () => void;
  onBack: () => void;
  onInspectionSaved: (listingId: number) => void;
}) {
  const toast = usePToast();
  const lotName = `${lot.make ?? ""} ${lot.model ?? ""}`.trim() || `Лот #${lot.listing_id}`;

  // --- загрузка существующего чека/истории ---
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  // --- шапка чека ---
  const [inspector, setInspector] = useState("");
  const [inspectedAt, setInspectedAt] = useState("");
  const [grade, setGrade] = useState<"" | PartnerGrade>("");
  const [summary, setSummary] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [imageUrl, setImageUrl] = useState("");

  // --- карта повреждений ---
  const [marks, setMarks] = useState<PartnerInspectionMark[]>([]);
  const [zonePanel, setZonePanel] = useState<PartnerBodyZone | null>(null);
  /** Индекс редактируемой отметки (в marks) или null — форма «добавить». */
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [formType, setFormType] = useState<PartnerMarkType>("scratch");
  const [formNote, setFormNote] = useState("");

  // --- чек-лист агрегатов ---
  const [items, setItems] = useState<ItemState>(emptyItems);

  // --- история машины ---
  const [events, setEvents] = useState<PartnerHistoryEvent[]>([]);

  // --- сохранение ---
  const [saving, setSaving] = useState(false);
  const [savingHistory, setSavingHistory] = useState(false);

  /** Существующий чек в форму (null → пустой редактор: машина без чека). */
  const applyReport = useCallback((report: PartnerInspectionReport | null) => {
    setInspector(report?.inspector ?? "");
    // date-инпуту нужен YYYY-MM-DD (бэк отдаёт datetime «2026-10-24T…»)
    setInspectedAt(report?.inspected_at ? report.inspected_at.slice(0, 10) : "");
    setGrade((report?.grade as PartnerGrade | null | undefined) ?? "");
    setSummary(report?.summary ?? "");
    setMarks(report?.marks ?? []);
    setImages(report?.images ?? []);
    const next = emptyItems();
    for (const it of report?.items ?? []) {
      if ((INSPECTION_ITEM_KEYS as readonly string[]).includes(it.key)) {
        next[it.key] = { status: it.status, note: it.note ?? "" };
      }
    }
    setItems(next);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadErr(null);
    fetchPartnerInspection(lot.listing_id)
      .then((report) => {
        if (alive) applyReport(report);
      })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
        else setLoadErr(partnerFail(e).text);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    // История — из публичной карточки (ListingOut.owner_history), ошибки тихо → []
    fetchListingOwnerHistory(lot.listing_id).then((list) => {
      if (alive) setEvents(list);
    });
    return () => {
      alive = false;
    };
  }, [lot.listing_id, applyReport, onUnauthorized]);

  // ---------- Карта повреждений ----------

  /** Открыть/закрыть панель зоны: клик по пустой зоне — добавить, по пину — список. */
  const openZone = (zone: PartnerBodyZone) => {
    if (zonePanel === zone) {
      setZonePanel(null);
      return;
    }
    setZonePanel(zone);
    setEditIdx(null);
    setFormType("scratch");
    setFormNote("");
  };

  /** Добавить/обновить отметку: pos берём из мини-карты (px 254x282). */
  const submitMark = () => {
    if (!zonePanel) return;
    const pos = ZONE_POS[zonePanel];
    const mark: PartnerInspectionMark = {
      zone: zonePanel,
      type: formType,
      note: formNote.trim() || null,
      pos: pos ? [pos.x, pos.y] : null,
    };
    setMarks((cur) => {
      if (editIdx == null) return [...cur, mark];
      const next = [...cur];
      next[editIdx] = mark;
      return next;
    });
    setEditIdx(null);
    setFormType("scratch");
    setFormNote("");
  };

  const removeMark = (idx: number) => {
    setMarks((cur) => cur.filter((_, i) => i !== idx));
    setEditIdx(null);
  };

  const editMark = (idx: number) => {
    const m = marks[idx];
    if (!m) return;
    setEditIdx(idx);
    setFormType(m.type);
    setFormNote(m.note ?? "");
  };

  /** Отметки по зонам (на зону может быть несколько — пин показывает счёт). */
  const marksByZone = useMemo(() => {
    const map = new Map<PartnerBodyZone, PartnerInspectionMark[]>();
    for (const m of marks) {
      const list = map.get(m.zone) ?? [];
      list.push(m);
      map.set(m.zone, list);
    }
    return map;
  }, [marks]);

  /** Цвет пина зоны — самый сильный дефект (MARK_SEVERITY). */
  const zonePinColor = (zone: PartnerBodyZone): string | null => {
    const list = marksByZone.get(zone);
    if (!list || list.length === 0) return null;
    for (const t of MARK_SEVERITY) {
      if (list.some((m) => m.type === t)) return MARK_COLOR[t];
    }
    return MARK_COLOR[list[0].type];
  };

  const zoneMarks = zonePanel ? (marksByZone.get(zonePanel) ?? []) : [];

  // ---------- Фото-ссылки ----------

  /** Партнёр без JWT: фото не грузим, только прямые URL (≤40). */
  const addImage = () => {
    const url = imageUrl.trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) {
      toast.show("Ссылка должна начинаться с http:// или https://", { error: true });
      return;
    }
    if (images.length >= 40) {
      toast.show("Максимум 40 ссылок на фото", { error: true });
      return;
    }
    setImages((cur) => [...cur, url]);
    setImageUrl("");
  };

  // ---------- Сохранение ----------

  const saveInspection = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await putPartnerInspection(lot.listing_id, {
        inspector: inspector.trim() || null,
        inspected_at: inspectedAt || null,
        grade: grade || null,
        summary: summary.trim() || null,
        marks: marks.map((m) => ({
          zone: m.zone,
          type: m.type,
          note: m.note ?? null,
          pos: m.pos ?? null,
        })),
        items: INSPECTION_ITEM_KEYS.map((k) => ({
          key: k,
          status: items[k].status,
          note: items[k].note.trim() || null,
        })),
        images,
      });
      onInspectionSaved(lot.listing_id);
      toast.show("✅ Чек сохранён — покупатели увидят его в карточке лота");
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setSaving(false);
    }
  };

  const addEvent = () => {
    if (events.length >= 50) {
      toast.show("Максимум 50 событий истории", { error: true });
      return;
    }
    setEvents((cur) => [...cur, { date: todayISO(), title: "", note: "" }]);
  };

  const patchEvent = (idx: number, patch: Partial<PartnerHistoryEvent>) => {
    setEvents((cur) => cur.map((e, i) => (i === idx ? { ...e, ...patch } : e)));
  };

  const removeEvent = (idx: number) => {
    setEvents((cur) => cur.filter((_, i) => i !== idx));
  };

  const saveHistory = async () => {
    if (savingHistory) return;
    const cleaned = events.map((e) => ({
      date: e.date,
      title: e.title.trim(),
      note: e.note?.trim() || null,
    }));
    if (cleaned.some((e) => !/^\d{4}-\d{2}-\d{2}$/.test(e.date) || !e.title)) {
      toast.show("У каждого события нужны дата и заголовок", { error: true });
      return;
    }
    setSavingHistory(true);
    try {
      await putPartnerHistory(lot.listing_id, cleaned);
 
      toast.show("✅ История сохранена");
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
      else toast.show(partnerFail(e).text, { error: true });
    } finally {
      setSavingHistory(false);
    }
  };

  // ---------- Рендер ----------

  if (loadErr) {
    return (
      <>
        <button type="button" className="btn pa-insp-back" onClick={onBack}>
          ← К списку лотов
        </button>
        <ErrorBlock
          description={loadErr}
          onAction={() => {
            // повторная загрузка: тот же лот, effect перезапустится сбросом ошибки
            setLoadErr(null);
            setLoading(true);
            fetchPartnerInspection(lot.listing_id)
              .then((r) => applyReport(r))
              .catch((e) => {
                if (e instanceof PartnerApiError && e.status === 401) onUnauthorized();
                else setLoadErr(partnerFail(e).text);
              })
              .finally(() => setLoading(false));
          }}
        />
      </>
    );
  }

  return (
    <>
      <button type="button" className="btn pa-insp-back" onClick={onBack}>
        ← К списку лотов
      </button>
      <h1 className="pa-title">
        {lotName}
        {lot.year ? `, ${lot.year}` : ""}
      </h1>
      <p className="pa-footnote">
        #{lot.listing_id}
        {lot.price != null && ` · ${fmtMoneyCompact(lot.price, lot.currency)}`} · чек осмотра и
        история машины
      </p>

      {loading ? (
        <SkelRows n={5} />
      ) : (
        <>
          {/* Шапка чека: центр/дата/оценка + комментарий */}
          <section className="pa-section">
            <h2 className="pa-section__title">Шапка чека</h2>
            <div className="pa-insp-head">
              <label className="pa-mkt-field">
                <span>Центр осмотра</span>
                <input
                  className="input"
                  placeholder="Например, Сервис на Гагарина"
                  aria-label="Центр осмотра"
                  maxLength={120}
                  value={inspector}
                  onChange={(e) => setInspector(e.target.value)}
                />
              </label>
              <label className="pa-mkt-field">
                <span>Дата осмотра</span>
                <input
                  className="input"
                  type="date"
                  aria-label="Дата осмотра"
                  value={inspectedAt}
                  onChange={(e) => setInspectedAt(e.target.value)}
                />
              </label>
              <label className="pa-mkt-field">
                <span>Оценка состояния</span>
                <select
                  className="input"
                  aria-label="Оценка состояния"
                  value={grade}
                  onChange={(e) => setGrade(e.target.value as "" | PartnerGrade)}
                >
                  <option value="">Не указана</option>
                  {INSPECTION_GRADES.map((g) => (
                    <option key={g} value={g}>
                      {GRADE_LABEL[g]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pa-mkt-field pa-mkt-field--wide">
                <span>Комментарий по чеку (до 1000)</span>
                <textarea
                  className="input"
                  rows={3}
                  maxLength={1000}
                  placeholder="Общее состояние, ключевые находки…"
                  aria-label="Комментарий по чеку"
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                />
              </label>
            </div>
          </section>

          {/* Диаграммы Encar: клик по зоне → отметить повреждение */}
          <section className="pa-section">
            <h2 className="pa-section__title">Карта повреждений</h2>
            <p className="pa-footnote">
              Нажмите на зону кузова, чтобы отметить повреждение; пин на схеме —
              список отметок зоны, цвет — по самому сильному дефекту.
            </p>
            <div className="pa-insp-diagrams">
              {DIAGRAMS.map((scheme) => (
                <div key={scheme.key} className="pa-insp-scheme">
                  <div className="pa-insp-scheme-title">{scheme.title}</div>
                  <div className="pa-insp-diagram" role="img" aria-label={scheme.title}>
                    <img className="pa-insp-diagram-img" src={scheme.img} alt="" loading="lazy" />
                    <span className="pa-insp-dir pa-insp-dir--front">ПЕРЕД</span>
                    <span className="pa-insp-dir pa-insp-dir--rear">ЗАД</span>
                    {/* Клики: прозрачные тач-зоны по координатам мини-карты */}
                    {zonesOf(scheme.key).map((zone) => {
                      const p = ZONE_POS[zone]!;
                      return (
                        <button
                          key={`h-${zone}`}
                          type="button"
                          className="pa-insp-hotspot"
                          style={{
                            left: `${(p.x / DIAGRAM_W) * 100}%`,
                            top: `${(p.y / DIAGRAM_H) * 100}%`,
                          }}
                          onClick={() => openZone(zone)}
                          aria-label={`Зона: ${ZONE_LABEL[zone]}`}
                          title={ZONE_LABEL[zone]}
                        />
                      );
                    })}
                    {/* Пины: по одному на зону с отметками, счётчик = число отметок */}
                    {[...marksByZone.entries()]
                      .filter(([zone]) => ZONE_POS[zone]?.d === scheme.key)
                      .map(([zone, list]) => {
                        const p = ZONE_POS[zone]!;
                        const color = zonePinColor(zone);
                        return (
                          <button
                            key={`p-${zone}`}
                            type="button"
                            className={`pa-insp-pin${
                              zonePanel === zone ? " pa-insp-pin--sel" : ""
                            }`}
                            style={{
                              left: `${(p.x / DIAGRAM_W) * 100}%`,
                              top: `${(p.y / DIAGRAM_H) * 100}%`,
                              ...(color ? { background: color } : null),
                            }}
                            onClick={() => openZone(zone)}
                            aria-label={`Повреждения: ${ZONE_LABEL[zone]}`}
                          >
                            {list.length}
                          </button>
                        );
                      })}
                  </div>
                </div>
              ))}
            </div>

            {/* Легенда типов повреждений */}
            <div className="pa-insp-legend">
              {INSPECTION_MARK_TYPES.map((t) => (
                <span key={t} className="pa-insp-legend-item">
                  <i style={{ background: MARK_COLOR[t] }} />
                  {MARK_LABEL[t]}
                </span>
              ))}
            </div>

            {/* Панель зоны: список отметок + форма добавить/изменить */}
            {zonePanel && (
              <div
                className="pa-insp-zone"
                role="group"
                aria-label={`Зона: ${ZONE_LABEL[zonePanel]}`}
              >
                <div className="pa-insp-zone-title">
                  {ZONE_LABEL[zonePanel]}
                  <button
                    type="button"
                    className="pa-insp-close"
                    aria-label="Закрыть зону"
                    onClick={() => setZonePanel(null)}
                  >
                    ✕
                  </button>
                </div>
                {zoneMarks.length === 0 ? (
                  <div className="pa-insp-zone-ok">Повреждений не отмечено</div>
                ) : (
                  zoneMarks.map((m, i) => {
                    const idx = marks.indexOf(m);
                    return (
                      <div key={`${m.zone}-${idx}`} className="pa-insp-markrow">
                        <i
                          className="pa-insp-markrow__dot"
                          style={{ background: MARK_COLOR[m.type] }}
                          aria-hidden="true"
                        />
                        <b>{MARK_LABEL[m.type]}</b>
                        {m.note && <span> — {m.note}</span>}
                        <span className="pa-insp-markrow__actions">
                          <button
                            type="button"
                            className="btn pa-btn-sm"
                            aria-label={`Изменить повреждение ${i + 1}`}
                            onClick={() => editMark(idx)}
                          >
                            Изменить
                          </button>
                          <button
                            type="button"
                            className="btn pa-btn-sm"
                            aria-label={`Удалить повреждение ${i + 1}`}
                            onClick={() => removeMark(idx)}
                          >
                            Удалить
                          </button>
                        </span>
                      </div>
                    );
                  })
                )}
                <form
                  className="pa-insp-markform"
                  onSubmit={(e) => {
                    e.preventDefault();
                    submitMark();
                  }}
                >
                  <select
                    className="input"
                    aria-label="Тип повреждения"
                    value={formType}
                    onChange={(e) => setFormType(e.target.value as PartnerMarkType)}
                  >
                    {INSPECTION_MARK_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {MARK_LABEL[t]}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    placeholder="Заметка (необязательно)"
                    aria-label="Заметка повреждения"
                    maxLength={300}
                    value={formNote}
                    onChange={(e) => setFormNote(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={submitMark}
                  >
                    {editIdx == null ? "Добавить повреждение" : "Сохранить повреждение"}
                  </button>
                </form>
              </div>
            )}
          </section>

          {/* Чек-лист агрегатов: строка × сегмент статуса + заметка */}
          <section className="pa-section">
            <h2 className="pa-section__title">Чек-лист агрегатов</h2>
            <div className="pa-insp-items">
              {INSPECTION_ITEM_KEYS.map((k) => (
                <div key={k} className="pa-insp-item">
                  <span className="pa-insp-item__label">{ITEM_LABEL[k]}</span>
                  <div className="pa-insp-seg" role="group" aria-label={`Статус: ${ITEM_LABEL[k]}`}>
                    {INSPECTION_ITEM_STATUSES.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`pa-insp-seg__btn${
                          items[k].status === s ? " pa-insp-seg__btn--active" : ""
                        }`}
                        aria-pressed={items[k].status === s}
                        aria-label={`${ITEM_LABEL[k]}: ${STATUS_LABEL[s]}`}
                        onClick={() =>
                          setItems((cur) => ({ ...cur, [k]: { ...cur[k], status: s } }))
                        }
                      >
                        {STATUS_LABEL[s]}
                      </button>
                    ))}
                  </div>
                  <input
                    className="input pa-insp-item__note"
                    placeholder="Заметка (необязательно)"
                    aria-label={`Заметка: ${ITEM_LABEL[k]}`}
                    maxLength={300}
                    value={items[k].note}
                    onChange={(e) =>
                      setItems((cur) => ({ ...cur, [k]: { ...cur[k], note: e.target.value } }))
                    }
                  />
                </div>
              ))}
            </div>
          </section>

          {/* Фото чека: партнёр без JWT — только прямые ссылки */}
          <section className="pa-section">
            <h2 className="pa-section__title">Фото чека</h2>
            <p className="pa-footnote">
              Ссылки на фото (до 40): загрузите снимки в любой фотохостинг и
              вставьте прямые URL — загрузка файлов партнёрскому ключу недоступна.
            </p>
            <form
              className="pa-insp-imgform"
              onSubmit={(e) => {
                e.preventDefault();
                addImage();
              }}
            >
              <input
                className="input"
                type="url"
                placeholder="https://…/photo.jpg"
                aria-label="Ссылка на фото"
                value={imageUrl}
                onChange={(e) => setImageUrl(e.target.value)}
              />
              <button type="button" className="btn" onClick={addImage}>
                Добавить фото
              </button>
            </form>
            {images.length > 0 && (
              <ul className="pa-insp-images">
                {images.map((url, i) => (
                  <li key={`${url}-${i}`} className="pa-insp-image">
                    <img src={url} alt="" loading="lazy" />
                    <span className="pa-insp-image__url">{url}</span>
                    <button
                      type="button"
                      className="btn pa-btn-sm"
                      aria-label={`Удалить фото ${i + 1}`}
                      onClick={() => setImages((cur) => cur.filter((_, j) => j !== i))}
                    >
                      Удалить
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="pa-insp-savebar">
            <button
              type="button"
              className="btn btn--primary"
              disabled={saving}
              onClick={() => void saveInspection()}
            >
              {saving ? "Сохраняем…" : "Сохранить чек"}
            </button>
            <span className="pa-footnote">
              {marks.length > 0
                ? `Отметок на карте: ${marks.length}`
                : "Карта без отметок — машина чистая"}
            </span>
          </div>

          {/* История машины: таймлайн событий (дата/заголовок/заметка) */}
          <section className="pa-section">
            <h2 className="pa-section__title">История машины</h2>
            <p className="pa-footnote">
              События владения и обслуживания (до 50) — покупатели увидят
              таймлайн в карточке лота.
            </p>
            <div className="pa-insp-hist">
              {events.length === 0 && (
                <div className="pa-insp-zone-ok">Событий пока нет — добавьте первое</div>
              )}
              {events.map((e, i) => (
                <div key={i} className="pa-insp-hist-row">
                  <input
                    className="input"
                    type="date"
                    aria-label="Дата события"
                    value={e.date}
                    onChange={(ev) => patchEvent(i, { date: ev.target.value })}
                  />
                  <input
                    className="input"
                    placeholder="Заголовок (до 120)"
                    aria-label="Заголовок события"
                    maxLength={120}
                    value={e.title}
                    onChange={(ev) => patchEvent(i, { title: ev.target.value })}
                  />
                  <input
                    className="input"
                    placeholder="Заметка (до 300)"
                    aria-label="Заметка события"
                    maxLength={300}
                    value={e.note ?? ""}
                    onChange={(ev) => patchEvent(i, { note: ev.target.value })}
                  />
                  <button
                    type="button"
                    className="btn btn--danger pa-btn-sm"
                    aria-label={`Удалить событие ${i + 1}`}
                    onClick={() => removeEvent(i)}
                  >
                    Удалить
                  </button>
                </div>
              ))}
            </div>
            <div className="pa-insp-savebar">
              <button type="button" className="btn" onClick={addEvent}>
                Добавить событие
              </button>
              <button
                type="button"
                className="btn btn--primary"
                disabled={savingHistory}
                onClick={() => void saveHistory()}
              >
                {savingHistory ? "Сохраняем…" : "Сохранить историю"}
              </button>
            </div>
          </section>
        </>
      )}
    </>
  );
}
