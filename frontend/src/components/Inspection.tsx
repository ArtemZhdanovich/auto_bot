/** Осмотр (миграция 0018): две схемы кузова с отметками повреждений
 *  («машинки» классического чека Encar: внешние панели + силовая структура)
 *  + чек-лист агрегатов. Данные — GET /api/listings/{id}/inspection.
 *  Панели — группы L/R-зон: на виде сбоку обе стороны совмещены, клик
 *  раскрывает отметки обеих. */

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

/** Панель схемы: группа зон (L/R совмещены на виде сбоку) + SVG-контур. */
interface PanelShape {
  zones: BodyZone[];
  d: string;
}

/** Схема «внешние панели» (외판 классического чека). Вид сбоку, перед слева. */
const OUTER_PANELS: PanelShape[] = [
  { zones: ["front_bumper"], d: "M8,78 Q8,68 22,66 L54,64 L54,144 L22,146 Q8,144 8,126 Z" },
  { zones: ["hood"], d: "M58,62 L150,50 L154,86 L58,96 Z" },
  { zones: ["roof"], d: "M160,44 L300,40 L303,82 L157,86 Z" },
  { zones: ["trunk"], d: "M370,78 L412,72 L412,140 L374,142 Z" },
  { zones: ["fender_fl", "fender_fr"], d: "M58,100 L154,90 L157,132 Q157,148 140,150 L120,152 Q100,152 96,150 L58,146 Z" },
  { zones: ["door_fl", "door_fr"], d: "M160,90 L230,86 L232,134 L160,136 Z" },
  { zones: ["door_rl", "door_rr"], d: "M234,86 L304,84 L306,134 L234,134 Z" },
  { zones: ["fender_rl", "fender_rr"], d: "M308,84 L366,80 L372,142 L310,138 Z" },
  { zones: ["rocker_l", "rocker_r"], d: "M160,140 L304,138 L304,152 L160,154 Z" },
];

/** Схема «силовая структура» (주요골격 классического чека). */
const FRAME_PANELS: PanelShape[] = [
  { zones: ["frame_front"], d: "M8,78 Q8,68 22,66 L104,60 L108,122 L22,126 Q8,124 8,104 Z" },
  { zones: ["frame_inside_l", "frame_inside_r"], d: "M112,62 L204,52 L207,114 L114,122 Z" },
  { zones: ["wheelhouse_fl", "wheelhouse_fr"], d: "M88,118 L152,112 L154,158 L90,162 Z" },
  { zones: ["pillar_a_l", "pillar_a_r"], d: "M160,46 L186,44 L188,88 L158,90 Z" },
  { zones: ["pillar_b_l", "pillar_b_r"], d: "M232,85 L254,84 L256,134 L234,134 Z" },
  { zones: ["pillar_c_l", "pillar_c_r"], d: "M298,42 L320,41 L323,82 L301,84 Z" },
  { zones: ["frame_floor"], d: "M160,140 L304,138 L304,154 L160,156 Z" },
  { zones: ["wheelhouse_rl", "wheelhouse_rr"], d: "M312,116 L372,112 L374,158 L314,160 Z" },
  { zones: ["frame_rear"], d: "M310,84 L410,74 L410,140 L312,140 Z" },
];

/** Колёса — рисуются поверх панелей, дают «арки». */
const WHEELS = [
  { cx: 112, cy: 148, r: 24 },
  { cx: 342, cy: 146, r: 24 },
];

const SCHEMES: { title: string; panels: PanelShape[] }[] = [
  { title: "Кузов — внешние панели", panels: OUTER_PANELS },
  { title: "Силовая структура", panels: FRAME_PANELS },
];

const ITEM_ORDER: ItemKey[] = [
  "engine", "transmission", "suspension", "brakes", "steering",
  "electronics", "interior", "optics", "glass", "tires", "ac", "leaks",
];

export function InspectionSection({ report }: { report: InspectionReport }) {
  const [selectedPanel, setSelectedPanel] = useState<PanelShape | null>(null);

  const hasMarks = report.marks.length > 0;
  const hasItems = report.items.length > 0;
  // Анонимные данные Encar не содержат повреждений по зонам и чек-листа —
  // вместо пустых схем показываем факты из summary списком.
  const facts =
    !hasMarks && !hasItems
      ? (report.summary ?? "")
          .split(" · ")
          .map((s) => s.trim())
          .filter(Boolean)
      : [];

  const markedZones = new Set(report.marks.map((m) => m.zone));
  const panelMarks = selectedPanel
    ? report.marks.filter((m) => selectedPanel.zones.includes(m.zone))
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

      {/* Фото чека (днище/салон) показываются в общей галерее карточки
          (Listing.tsx: galleryImages) — здесь дублировать не нужно */}

      {hasMarks && (
      <>
      {/* Две «машинки» классического чека: тап по панели — детали */}
      <div className="inspection__schemes">
        {SCHEMES.map((scheme) => {
          const hasSchemeMarks = scheme.panels.some((p) =>
            p.zones.some((z) => markedZones.has(z)),
          );
          return (
            <div key={scheme.title} className="inspection__scheme">
              <div className="inspection__scheme-title">
                {scheme.title}
                {!hasSchemeMarks && (
                  <span className="inspection__scheme-ok"> — без дефектов</span>
                )}
              </div>
              <svg
                className="inspection__car"
                viewBox="0 0 420 172"
                role="img"
                aria-label={scheme.title}
              >
                {scheme.panels.map((panel) => {
                  const color = panel.zones
                    .map((z) => zoneColor(report.marks, z))
                    .find((c) => c !== "transparent");
                  const isSel =
                    selectedPanel != null &&
                    panel.zones.some((z) => selectedPanel.zones.includes(z));
                  return (
                    <path
                      key={panel.zones.join(",")}
                      d={panel.d}
                      fill={color ? color : undefined}
                      className={`inspection__zone${color ? " inspection__zone--mark" : ""}${isSel ? " inspection__zone--sel" : ""}`}
                      onClick={() => setSelectedPanel(isSel ? null : panel)}
                    >
                      <title>
                        {panel.zones.map((z) => ZONE_LABEL[z]).join(" / ")}
                      </title>
                    </path>
                  );
                })}
                {WHEELS.map((w, i) => (
                  <circle
                    key={i}
                    cx={w.cx}
                    cy={w.cy}
                    r={w.r}
                    className="inspection__wheel"
                  />
                ))}
                <text x="26" y="36" className="inspection__axis">ПЕРЕД</text>
                <text x="378" y="36" className="inspection__axis">ЗАД</text>
              </svg>
            </div>
          );
        })}
      </div>

      <div className="inspection__legend">
        {MARK_SEVERITY.map((t) => (
          <span key={t} className="inspection__legend-item">
            <i style={{ background: MARK_COLOR[t] }} />
            {MARK_LABEL[t]}
          </span>
        ))}
      </div>

      {selectedPanel ? (
        <div className="inspection__zone-details">
          <div className="inspection__zone-title">
            {selectedPanel.zones.map((z) => ZONE_LABEL[z]).join(" / ")}
            <button
              className="inspection__close"
              onClick={() => setSelectedPanel(null)}
              aria-label="Закрыть"
            >
              ✕
            </button>
          </div>
          {panelMarks.length === 0 ? (
            <div className="inspection__zone-ok">Повреждений не зафиксировано</div>
          ) : (
            panelMarks.map((m, i) => (
              <div key={i} className="inspection__zone-mark">
                <b>{MARK_LABEL[m.type]}</b>
                {m.note && <span> — {m.note}</span>}
              </div>
            ))
          )}
        </div>
      ) : (
        markedZones.size > 0 && (
          <div className="inspection__hint">
            🔎 Нажмите на панель кузова, чтобы увидеть повреждения
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
