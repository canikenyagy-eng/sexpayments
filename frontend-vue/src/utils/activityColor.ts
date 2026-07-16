// Single source of truth for the «Активность» trader-count colour scale, shared
// by the chart (per-bar fill) and the tab legend so the two never diverge.
// Thresholds: 0–15 red → 15–30 yellow → 30–45 green → 45+ solid green.

type Stop = [number, [number, number, number]]

const COLOR_STOPS: Stop[] = [
  [0, [226, 75, 74]], // #e24b4a
  [15, [234, 179, 8]], // #eab308
  [30, [34, 197, 94]], // #22c55e
  [45, [22, 163, 74]], // #16a34a
]

const rgb = (c: [number, number, number]) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`

/** Interpolated red→yellow→green colour for a bucket's unique-trader count. */
export function colorForTraders(count: number): string {
  if (count <= COLOR_STOPS[0][0]) return rgb(COLOR_STOPS[0][1])
  const last = COLOR_STOPS[COLOR_STOPS.length - 1]
  if (count >= last[0]) return rgb(last[1])
  for (let i = 0; i < COLOR_STOPS.length - 1; i++) {
    const [a, ca] = COLOR_STOPS[i]
    const [b, cb] = COLOR_STOPS[i + 1]
    if (count >= a && count <= b) {
      const f = (count - a) / (b - a)
      return rgb([
        Math.round(ca[0] + (cb[0] - ca[0]) * f),
        Math.round(ca[1] + (cb[1] - ca[1]) * f),
        Math.round(ca[2] + (cb[2] - ca[2]) * f),
      ])
    }
  }
  return rgb(last[1])
}

/** Legend rows for the tab — colour swatch + label per threshold band. */
export const TRADER_LEGEND: { label: string; color: string }[] = [
  { label: '0–15 трейдеров', color: rgb(COLOR_STOPS[0][1]) },
  { label: '15–30', color: rgb(COLOR_STOPS[1][1]) },
  { label: '30–45', color: rgb(COLOR_STOPS[2][1]) },
  { label: '45+', color: rgb(COLOR_STOPS[3][1]) },
]
