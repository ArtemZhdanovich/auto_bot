/** Осмотр (миграция 0018): две диаграммы кузова Encar (вид сверху — официальные
 *  картинки классического чека: внешние панели + силовая структура) с маркерами
 *  повреждений + чек-лист агрегатов. Данные — GET /api/listings/{id}/inspection;
 *  координаты маркеров (mark.pos, px диаграммы 254x282) считает адаптер. */

import { useState } from "react";

import {
  GRADE_LABEL,
  ITEM_LABEL,
  MARK_COLOR,
  MARK_LABEL,
  MARK_SEVERITY,
  STATUS_ICON,
  ZONE_LABEL,
  type BodyZone,
  type InspectionReport,
  type ItemKey,
} from "../lib/inspection";

/** Официальные диаграммы чека Encar: вид сверху, 254x282, перед вверху. */
const DIAGRAM_BASE = "https://www.encar.com/images/pop/check";
const DIAGRAM_W = 254;
const DIAGRAM_H = 282;

const DIAGRAMS: { title: string; img: string; zones: Set<BodyZone> }[] = [
  {
    title: "Кузов — внешние панели",
    img: `${DIAGRAM_BASE}/img_inspection01.gif`,
    zones: new Set<BodyZone>([
      "hood", "fender_fl", "fender_fr", "door_fl", "door_fr",
      "door_rl", "door_rr", "rocker_l", "rocker_r", "fender_rl",
      "fender_rr", "trunk", "roof",
    ]),
  },
  {
    title: "Силовая структура",
    img: `${DIAGRAM_BASE}/img_inspection02_new.gif`,
    zones: new Set<BodyZone>([
      "frame_front", "frame_inside_l", "frame_inside_r",
      "wheelhouse_fl", "wheelhouse_fr", "pillar_a_l", "pillar_a_r",
      "pillar_b_l", "pillar_b_r", "pillar_c_l", "pillar_c_r",
      "frame_floor", "wheelhouse_rl", "wheelhouse_rr", "frame_rear",
    ]),
  },
];

const ITEM_ORDER: ItemKey[] = [
  "engine", "transmission", "suspension", "brakes", "steering",
  "electronics", "interior", "optics", "glass", "tires", "ac", "leaks",
];

export function InspectionSection({ report }: { report: InspectionReport }) {
  const [selectedZone, setSelectedZone] = useState<BodyZone | null>(null);

  const hasMarks = report.marks.length > 0;
  const hasItems = report.items.length > 0;
  // Совсем пустой чек (нет ни карты, ни чек-листа) — показываем хотя бы
  // сводку: факты из summary списком.
  const facts =
    !hasMarks && !hasItems
      ? (report.summary ?? "")
          .split(" · ")
          .map((s) => s.trim())
          .filter(Boolean)
      : [];

  // Метки по зонам (на зону может быть несколько отметок — берём худшую
  // для цвета пина, детали показываем все).
  const byZone = new Map<BodyZone, typeof report.marks>();
  for (const m of report.marks) {
    const list = byZone.get(m.zone) ?? [];
    list.push(m);
    byZone.set(m.zone, list);
  }
  const zoneMarks = selectedZone ? (byZone.get(selectedZone) ?? []) : [];
  const warnCount = report.items.filter((i) => i.status === "warn").length;
  const defectCount = report.items.filter((i) => i.status === "defect").length;
  const items = ITEM_ORDER.filter((k) => report.items.some((i) => i.key === k));

  return (
    <div className="inspection">
      <div className="inspection__head">
        {report.grade && (
          <span className={`inspection__grade inspection__grade--${report.grade}`}>
            {GRADE_LABEL[report.grade]}
          </span>
        )}
        <span className="inspection__meta">
          {report.inspected_at &&
            `осмотр ${new Date(report.inspected_at).toLocaleDateString("ru")}`}
          {report.inspector && ` · ${report.inspector}`}
          {report.source === "encar" && " · данные Encar"}
        </span>
      </div>

      {/* Фото чека (днище/салон) показываются в общей галерее карточки
          (Listing.tsx: galleryImages) — здесь дублировать не нужно */}

      {/* Диаграммы чека Encar: показываем ВСЕГДА, даже без отметок —
          пустая карта + зелёная плашка понятнее, чем пропавшая секция.
          Тап по маркеру — детали зоны. */}
      <div className="inspection__schemes">
        {DIAGRAMS.map((scheme) => {
          const pins = [...byZone.entries()].filter(([z]) => scheme.zones.has(z));
          return (
            <div key={scheme.title} className="inspection__scheme">
              <div className="inspection__scheme-title">
                {scheme.title}
                {pins.length === 0 && (
                  <span className="inspection__scheme-ok"> — без дефектов</span>
                )}
              </div>
              <div
                className="inspection__diagram"
                role="img"
                aria-label={scheme.title}
              >
                <img
                  className="inspection__diagram-img"
                  src={scheme.img}
                  alt=""
                  loading="lazy"
                />
                <span className="inspection__dir inspection__dir--front">ПЕРЕД</span>
                <span className="inspection__dir inspection__dir--rear">ЗАД</span>
                {pins.map(([zone, marks]) => {
                  const pos = marks[0].pos;
                  if (!pos || pos.length < 2) return null;
                  const color = marks
                    .map((m) => MARK_COLOR[m.type])
                    .find(Boolean);
                  return (
                    <button
                      key={zone}
                      className={`inspection__pin${
                        selectedZone === zone ? " inspection__pin--sel" : ""
                      }`}
                      style={{
                        left: `${(pos[0] / DIAGRAM_W) * 100}%`,
                        top: `${(pos[1] / DIAGRAM_H) * 100}%`,
                        ...(color ? { background: color } : null),
                      }}
                      onClick={() =>
                        setSelectedZone(selectedZone === zone ? null : zone)
                      }
                      aria-label={ZONE_LABEL[zone]}
                    >
                      {String(marks.length)}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {hasMarks ? (
        <>
      <div className="inspection__legend">
        {MARK_SEVERITY.map((t) => (
          <span key={t} className="inspection__legend-item">
            <i style={{ background: MARK_COLOR[t] }} />
            {MARK_LABEL[t]}
          </span>
        ))}
      </div>

      {selectedZone ? (
        <div className="inspection__zone-details">
          <div className="inspection__zone-title">
            {ZONE_LABEL[selectedZone]}
            <button
              className="inspection__close"
              onClick={() => setSelectedZone(null)}
              aria-label="Закрыть"
            >
              ✕
            </button>
          </div>
          {zoneMarks.length === 0 ? (
            <div className="inspection__zone-ok">Повреждений не зафиксировано</div>
          ) : (
            zoneMarks.map((m, i) => (
              <div key={i} className="inspection__zone-mark">
                <b>{MARK_LABEL[m.type]}</b>
                {m.note && <span> — {m.note}</span>}
              </div>
            ))
          )}
        </div>
      ) : (
        byZone.size > 0 && (
          <div className="inspection__hint">
            🔎 Нажмите на маркер на схеме, чтобы увидеть повреждения
          </div>
        )
      )}
        </>
      ) : (
        /* Пустые marks ≠ пустой чек: машина чистая или чек без карты
           повреждений — объясняем зелёной плашкой, почему пинов нет */
        <div className="insp-clean" role="status">
          <span className="insp-clean__check" aria-hidden="true">✓</span>
          Повреждений по чеку не зафиксировано
        </div>
      )}

      {/* Чек-лист агрегатов */}
      {items.length > 0 && (
        <div className="inspection__checklist">
          <div className="inspection__checklist-head">
            Чек-лист
            {(warnCount > 0 || defectCount > 0) && (
              <span className="inspection__checklist-badge">
                {defectCount > 0 && ` ❌ ${defectCount}`}
                {warnCount > 0 && ` ⚠️ ${warnCount}`}
              </span>
            )}
          </div>
          {items.map((k) => {
            const item = report.items.find((i) => i.key === k)!;
            return (
              <div
                key={k}
                className={`inspection__item inspection__item--${item.status}`}
              >
                <span className="inspection__item-icon">{STATUS_ICON[item.status]}</span>
                <span className="inspection__item-label">{ITEM_LABEL[k]}</span>
                {item.note && (
                  <span className="inspection__item-note">{item.note}</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {facts.length > 0 ? (
        <ul className="inspection__facts">
          {facts.map((f, i) => (
            <li key={i} className="inspection__fact">
              {f}
            </li>
          ))}
        </ul>
      ) : (
        report.summary && (
          <div className="inspection__summary">💬 {report.summary}</div>
        )
      )}
    </div>
  );
}
