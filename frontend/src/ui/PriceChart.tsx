/** SVG-график истории цены + диапазон рынка (без chart-библиотек, MVP). */

import { buildChartData } from "../lib/analysis";
import { formatCompact } from "../lib/analysis";

interface Props {
  history: { price: number; recorded_at: string }[];
  comps: { min: number; max: number } | null;
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function PriceChart({ history, comps }: Props) {
  const spec = buildChartData(history, comps);
  if (!spec) return null;
  const path = spec.line
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
  // Заливка под линией (area)
  const first = spec.line[0];
  const last = spec.line[spec.line.length - 1];
  const area = `${path} L${last.x.toFixed(1)},112 L${first.x.toFixed(1)},112 Z`;
  const dates = history.filter((h) => h.price > 0).map((h) => h.recorded_at);
  const price = history.filter((h) => h.price > 0).slice(-1)[0]?.price;

  return (
    <div className="price-chart">
      <svg viewBox="0 0 320 120" width="100%" role="img" aria-label="График истории цены">
        <defs>
          <linearGradient id="pcFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4d7cfe" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#4d7cfe" stopOpacity="0" />
          </linearGradient>
        </defs>
        {spec.band && (
          <rect
            x={8}
            y={spec.band.top}
            width={304}
            height={Math.max(2, spec.band.bottom - spec.band.top)}
            fill="rgba(48,164,108,0.13)"
            rx={4}
          />
        )}
        <path d={area} fill="url(#pcFill)" stroke="none" />
        <path
          d={path}
          fill="none"
          stroke="#4d7cfe"
          strokeWidth={2.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {spec.current && (
          <g className="price-chart__dot">
            <circle cx={spec.current.x} cy={spec.current.y} r={7} fill="#4d7cfe" opacity={0.25} />
            <circle
              cx={spec.current.x}
              cy={spec.current.y}
              r={4.5}
              fill="#4d7cfe"
              stroke="#fff"
              strokeWidth={2}
            />
          </g>
        )}
      </svg>
      {price != null && spec.current && (
        <div
          className="price-chart__price"
          style={{ left: `${Math.min(78, Math.max(4, (spec.current.x / 320) * 100))}%` }}
        >
          {formatCompact(price)}
        </div>
      )}
      <div className="price-chart__scale">
        <span>{formatCompact(spec.scale.min)}</span>
        <span className="price-chart__dates">
          {dates.length > 0 && `${shortDate(dates[0])} — ${shortDate(dates[dates.length - 1])}`}
        </span>
        <span>{formatCompact(spec.scale.max)}</span>
      </div>
      {spec.band && <div className="price-chart__legend">◼ рыночный диапазон</div>}
    </div>
  );
}
