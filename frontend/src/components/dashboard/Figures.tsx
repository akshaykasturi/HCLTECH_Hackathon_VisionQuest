// Dashboard building blocks (dataviz method): stat tiles, single-hue meter lists, section headings.
// Values are always printed as text next to their bar, so nothing depends on hover or colour.
import type { ReactNode } from "react";
import { Card } from "../common/ui";

export function SectionHeading({ id, title, children }: { id: string; title: string; children?: ReactNode }) {
  return (
    <div className="mb-3">
      <h2 id={id} className="text-lg font-semibold text-ink">{title}</h2>
      {children && <p className="mt-0.5 text-sm text-muted">{children}</p>}
    </div>
  );
}

/** Stat tile: label (sentence case), value (proportional figures), one line of context, optional badge. */
export function StatTile({ label, value, detail, badge }: { label: string; value: string; detail: ReactNode; badge?: ReactNode }) {
  return (
    <Card as="article" aria-label={label} className="p-5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted">{label}</p>
        {badge}
      </div>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-ink">{value}</p>
      <p className="mt-1 text-sm text-muted">{detail}</p>
    </Card>
  );
}

export interface MeterItem {
  name: string;
  share: number | null; // 0..1 of the bar; null draws no bar
  figure: string;       // printed beside the bar
}

/** One hue for every bar (names are not a ranking of colour); the track is a lighter step of the same blue. */
export function MeterList({ label, items }: { label: string; items: MeterItem[] }) {
  return (
    <ul aria-label={label} className="divide-y divide-slate-100">
      {items.map(({ name, share, figure }) => (
        <li key={name} className="grid gap-x-4 gap-y-1.5 px-5 py-3 sm:grid-cols-[minmax(8rem,12rem)_1fr_minmax(7rem,9rem)] sm:items-center">
          <span className="text-sm font-medium text-ink">{name}</span>
          <span aria-hidden className="h-2.5 overflow-hidden rounded-full bg-blue-100" title={`${name}: ${figure}`}>
            {share !== null && share > 0 && (
              <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.max(Math.min(share, 1) * 100, 1.5)}%` }} />
            )}
          </span>
          <span className="text-sm text-slate-700 sm:text-right" style={{ fontVariantNumeric: "tabular-nums" }}>{figure}</span>
        </li>
      ))}
    </ul>
  );
}
