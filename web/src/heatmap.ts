import type { HeatmapDay } from "./types";

const clock = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** The time-of-day rows to draw, in order, across every day. */
export function rowTimes(days: HeatmapDay[]): string[] {
  const seen = new Set<string>();
  for (const day of days) for (const cell of day.cells) seen.add(clock(cell.start_at));
  return [...seen].sort();
}

/** free count by "HH:MM" for one day. */
export function dayLookup(day: HeatmapDay): Map<string, number> {
  return new Map(day.cells.map((c) => [clock(c.start_at), c.free]));
}

/** 0 (nobody free) to 1 (everyone free). */
export function shade(free: number, max: number): number {
  return max > 0 ? Math.min(1, Math.max(0, free / max)) : 0;
}

/** The cell with the most people free, earliest first, to point at in the summary. */
export function bestCell(days: HeatmapDay[]): { start_at: string; free: number } | null {
  let best: { start_at: string; free: number } | null = null;
  for (const day of days) {
    for (const cell of day.cells) {
      if (!best || cell.free > best.free) best = cell;
    }
  }
  return best;
}
