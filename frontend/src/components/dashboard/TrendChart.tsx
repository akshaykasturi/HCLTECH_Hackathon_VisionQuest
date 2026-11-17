// Resolution trend: one series (median hours per month), so no legend: the card title names it.
// Dataviz specs: 2px line, >= 8px points with a 2px surface ring, hairline solid grid, clean y ticks,
// every point focusable with its values (hover title + accessible name), and a table view twin.
import { formatHours } from "../../lib/format";

export interface TrendPoint {
  month: string;        // "YYYY-MM"
  medianHours: number;
  resolved: number;
}

const W = 640;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 32, left: 48 };
const monthName = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return monthName.format(new Date(Date.UTC(y, m - 1, 1)));
}

/** Round the axis top up to a clean step: 1, 2, 5 x 10^n. */
function niceMax(value: number): number {
  if (value <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((s) => s * exp >= value)! * exp;
  return step;
}

export function TrendChart({ points }: { points: TrendPoint[] }) {
  const top = niceMax(Math.max(...points.map((p) => p.medianHours)) * 1.1);
  const ticks = [0, top / 2, top];
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.medianHours).toFixed(1)}`).join(" ");

  return (
    <figure className="px-5 pb-4 pt-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="group" aria-label="Median time to resolve by month">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="#E2E8F0" strokeWidth={1} />
            <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-slate-500 text-[11px]"
                  style={{ fontVariantNumeric: "tabular-nums" }}>{formatHours(t)}</text>
          </g>
        ))}
        <path d={path} fill="none" stroke="#1D4ED8" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => {
          const name = `${monthLabel(p.month)}: median ${formatHours(p.medianHours)}, ${p.resolved} resolved`;
          return (
            <g key={p.month} tabIndex={0} role="img" aria-label={name}
               className="cursor-default focus:outline-none [&:focus>circle.ring]:stroke-primary">
              <title>{name}</title>
              <circle cx={x(i)} cy={y(p.medianHours)} r={12} fill="transparent" />{/* hit area > mark */}
              <circle className="ring" cx={x(i)} cy={y(p.medianHours)} r={5} fill="#1D4ED8" stroke="#FFFFFF" strokeWidth={2} />
            </g>
          );
        })}
        {points.map((p, i) => (
          (points.length <= 8 || i % Math.ceil(points.length / 8) === 0) && (
            <text key={`l-${p.month}`} x={x(i)} y={H - 10} textAnchor="middle" className="fill-slate-500 text-[11px]">
              {monthLabel(p.month).replace(" 20", " ’")}
            </text>
          )
        ))}
      </svg>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">View as table</summary>
        <table className="mt-2 w-full" style={{ fontVariantNumeric: "tabular-nums" }}>
          <caption className="sr-only">Median time to resolve by month</caption>
          <thead className="text-left text-xs uppercase tracking-wide text-muted">
            <tr><th scope="col" className="py-1 font-medium">Month</th><th scope="col" className="py-1 text-right font-medium">Median</th><th scope="col" className="py-1 text-right font-medium">Resolved</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-slate-700">
            {points.map((p) => (
              <tr key={p.month}>
                <th scope="row" className="py-1 text-left font-normal">{monthLabel(p.month)}</th>
                <td className="py-1 text-right">{formatHours(p.medianHours)}</td>
                <td className="py-1 text-right">{p.resolved.toLocaleString("en-GB")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
