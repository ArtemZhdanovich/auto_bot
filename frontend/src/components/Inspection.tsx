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

/** Общий силуэт кузова (вид сбоку, перед слева): профиль + вырезы арок. */
const BODY_D =
  "M20,122 C13,110 14,101 24,97 L166,76 L213,48 L290,43 L327,73 L398,79 " +
  "C405,88 407,120 403,137 L356,142 A26,26 0 0 0 304,142 L140,142 " +
  "A26,26 0 0 0 88,142 L28,142 C18,136 16,128 20,122 Z";

/** Остекление (лобовое/крыша/заднее) — рисуется под панелями. */
const GLASS_D = "M174,76 L216,52 L286,47 L316,73 Z";

/** Детали: B-стойка, ручки дверей. */
const SEAM_D = ["M233,52 L238,74", "M194,98 h16", "M244,94 h16"];

/** Схема «внешние панели» (외판 классического чека). Вид сбоку, перед слева. */
const OUTER_PANELS: PanelShape[] = [
  { zones: ["front_bumper"], d: "M22,99 L36,96 L37,140 L26,140 C15,132 14,110 22,99 Z" },
  { zones: ["hood"], d: "M40,94 L164,79 L166,93 L42,108 Z" },
  {
    zones: ["fender_fl", "fender_fr"],
    d: "M36,112 L166,97 L168,140 L140,141 A26,26 0 0 0 88,141 L66,148 L38,146 Z",
  },
  { zones: ["roof"], d: "M217,50 L288,45 L290,60 L219,65 Z" },
  { zones: ["door_fl", "door_fr"], d: "M172,97 L231,92 L233,142 L174,144 Z" },
  { zones: ["door_rl", "door_rr"], d: "M237,92 L297,87 L299,142 L239,142 Z" },
  { zones: ["rocker_l", "rocker_r"], d: "M146,147 L300,145 L300,159 L148,161 Z" },
  {
    zones: ["fender_rl", "fender_rr"],
    d: "M303,88 L332,75 L396,81 L398,138 L356,141 A26,26 0 0 0 304,141 L300,120 Z",
  },
  { zones: ["trunk"], d: "M336,77 L397,82 L398,94 L337,89 Z" },
];

/** Схема «силовая структура» (주요골격 классического чека). */
const FRAME_PANELS: PanelShape[] = [
  { zones: ["frame_front"], d: "M20,99 L88,92 L88,142 L26,142 C15,132 14,110 20,99 Z" },
  { zones: ["frame_inside_l", "frame_inside_r"], d: "M92,96 L166,88 L168,104 L94,112 Z" },
  { zones: ["wheelhouse_fl", "wheelhouse_fr"], d: "M64,118 L88,116 L88,144 L62,146 Z" },
  { zones: ["pillar_a_l", "pillar_a_r"], d: "M174,96 L205,54 L217,56 L187,97 Z" },
  { zones: ["pillar_b_l", "pillar_b_r"], d: "M236,92 L250,91 L252,143 L238,143 Z" },
  { zones: ["pillar_c_l", "pillar_c_r"], d: "M289,46 L299,45 L330,74 L318,77 Z" },
  { zones: ["frame_floor"], d: "M146,146 L302,144 L302,158 L148,160 Z" },
  { zones: ["wheelhouse_rl", "wheelhouse_rr"], d: "M304,100 L356,96 L356,142 L304,142 Z" },
  {
    zones: ["frame_rear"],
    d: "M336,84 L398,80 C403,96 403,124 399,138 L358,140 Z M310,126 L356,124 L356,142 L310,142 Z",
  },
];

/** Колёса — рисуются поверх панелей, дают «арки»; r диска — 10. */
const WHEELS = [
  { cx: 114, cy: 142, r: 26 },
  { cx: 330, cy: 142, r: 26 },
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
                viewBox="0 0 420 176"
                role="img"
                aria-label={scheme.title}
              >
                <path d={BODY_D} className="inspection__body" />
                <path d={GLASS_D} className="inspection__glass" />
                {SEAM_D.map((d, i) => (
                  <path key={`seam-${i}`} d={d} className="inspection__seam" />
                ))}
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
                    key={`tire-${i}`}
                    cx={w.cx}
                    cy={w.cy}
                    r={w.r}
                    className="inspection__wheel"
                  />
                ))}
                {WHEELS.map((w, i) => (
                  <circle
                    key={`hub-${i}`}
                    cx={w.cx}
                    cy={w.cy}
                    r={10}
                    className="inspection__hub"
                  />
                ))}
                <text x="30" y="30" className="inspection__axis">ПЕРЕД</text>
                <text x="390" y="30" className="inspection__axis">ЗАД</text>
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
