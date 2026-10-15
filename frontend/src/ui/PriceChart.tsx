/** SVG-график истории цены + диапазон рынка (без chart-библиотек, MVP). */

import { buildChartData } from "../lib/analysis";

interface Props {
  history: { price: number; recorded_at: string }[];
  comps: { min: number; max: number } | null;
}

export function PriceChart({ history, comps }: Props) {
  const spec = buildChartData(history, comps);
  if (!spec) return null;
  const path = spec.line.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  return (
    <div className="price-chart">
      <svg viewBox="0 0 320 120" width="100%" role="img" aria-label="График истории цены">
        {spec.band && (
          <rect
            x={8}
            y={spec.band.top}
            width={304}
            height={Math.max(2, spec.band.bottom - spec.band.top)}
            fill="rgba(48,164,108,0.14)"
            rx={4}
          />
        )}
        <path d={path} fill="none" stroke="#4d7cfe" strokeWidth={2.5} strokeLinejoin="round" />
        {spec.current && (
          <circle cx={spec.current.x} cy={spec.current.y} r={5} fill="#4d7cfe" stroke="#fff" strokeWidth={2} />
        )}
      </svg>
      <div className="price-chart__scale">
        <span>{spec.scale.min.toLocaleString("ru-RU")}</span>
        <span>{spec.scale.max.toLocaleString("ru-RU")}</span>
      </div>
      {spec.band && <div className="price-chart__legend">◼ зелёная зона — рыночный диапазон</div>}
    </div>
  );
}
