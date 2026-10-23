/** Осмотр (миграция 0018): SVG-карта кузова с повреждениями + чек-лист агрегатов.
 *  Данные — GET /api/listings/{id}/inspection; вносятся админом (PUT). */

import { useState } from "react";

import {
  GRADE_LABEL,
  ITEM_LABEL,
  MARK_COLOR,
  MARK_LABEL,
  MARK_SEVERITY,
  STATUS_ICON,
  ZONE_LABEL,
  zoneColor,
  type BodyZone,
  type InspectionReport,
  type ItemKey,
} from "../lib/inspection";

/** Схема кузова вид сверху (перед вверху). */
const ZONE_SHAPES: { zone: BodyZone; d: string }[] = [
  { zone: "front_bumper", d: "M72 8 Q60 8 60 26 L60 48 L240 48 L240 26 Q240 8 228 8 Z" },
  { zone: "fender_fl", d: "M60 54 L96 54 L96 168 L60 168 Z" },
  { zone: "fender_fr", d: "M204 54 L240 54 L240 168 L204 168 Z" },
  { zone: "hood", d: "M102 54 L198 54 L198 168 L102 168 Z" },
  { zone: "door_fl", d: "M60 174 L96 174 L96 318 L60 318 Z" },
  { zone: "door_fr", d: "M204 174 L240 174 L240 318 L204 318 Z" },
  { zone: "rocker_l", d: "M60 324 L96 324 L96 356 L60 356 Z" },
  { zone: "rocker_r", d: "M204 324 L240 324 L240 356 L204 356 Z" },
  { zone: "door_rl", d: "M60 362 L96 362 L96 486 L60 486 Z" },
  { zone: "door_rr", d: "M204 362 L240 362 L240 486 L204 486 Z" },
  { zone: "fender_rl", d: "M60 492 L96 492 L96 552 L60 552 Z" },
  { zone: "fender_rr", d: "M204 492 L240 492 L240 552 L204 552 Z" },
  { zone: "roof", d: "M102 174 L198 174 L198 486 L102 486 Z" },
  { zone: "trunk", d: "M72 492 L228 492 L228 580 Q228 598 216 598 L84 598 Q72 598 72 580 Z" },
];

const ITEM_ORDER: ItemKey[] = [
  "engine", "transmission", "suspension", "brakes", "steering",
  "electronics", "interior", "optics", "glass", "tires", "ac", "leaks",
];

export function InspectionSection({ report }: { report: InspectionReport }) {
  const [selectedZone, setSelectedZone] = useState<BodyZone | null>(null);

  const hasMarks = report.marks.length > 0;
  const hasItems = report.items.length > 0;
  // Анонимные данные Encar не содержат повреждений по зонам и чек-листа —
  // вместо пустой карты показываем факты из summary списком.
  const facts =
    !hasMarks && !hasItems
      ? (report.summary ?? "")
          .split(" · ")
          .map((s) => s.trim())
          .filter(Boolean)
      : [];

  const defectZones = new Set(report.marks.map((m) => m.zone));
  const zoneMarks = selectedZone
    ? report.marks.filter((m) => m.zone === selectedZone)
    : [];
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

      {/* Фото чека осмотра (днище/салон) — из чека 성능점검기록부 */}
      {(report.images?.length ?? 0) > 0 && (
        <div className="inspection__photos">
          <div className="inspection__photos-title">Фото осмотра</div>
          <div className="inspection__photos-grid">
            {report.images!.map((src, i) => (
              <a
                key={src}
                className="inspection__photo"
                href={src}
                target="_blank"
                rel="noreferrer"
              >
                <img src={src} alt={`Фото осмотра ${i + 1}`} loading="lazy" />
              </a>
            ))}
          </div>
        </div>
      )}

      {hasMarks && (
      <>
      {/* SVG-карта кузова: тап по зоне — детали повреждений */}
      <svg
        className="inspection__map"
        viewBox="0 0 300 606"
        role="img"
        aria-label="Карта повреждений кузова"
      >
        {ZONE_SHAPES.map(({ zone, d }) => {
          const color = zoneColor(report.marks, zone);
          const isSel = selectedZone === zone;
          return (
            <path
              key={zone}
              d={d}
              fill={color !== "transparent" ? color : undefined}
              className={`inspection__zone${color !== "transparent" ? " inspection__zone--mark" : ""}${isSel ? " inspection__zone--sel" : ""}`}
              onClick={() => setSelectedZone(isSel ? null : zone)}
            >
              <title>{ZONE_LABEL[zone]}</title>
            </path>
          );
        })}
        <text x="150" y="26" className="inspection__axis">ПЕРЕД</text>
        <text x="150" y="594" className="inspection__axis">ЗАД</text>
      </svg>

      <div className="inspection__legend">
        {MARK_SEVERITY.slice().reverse().map((t) => (
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
        defectZones.size > 0 && (
          <div className="inspection__hint">
            🔎 Нажмите на зону кузова, чтобы увидеть повреждения
          </div>
        )
      )}
      </>
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
