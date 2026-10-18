import { day as dayText, startOfDay, time } from "./format";
import type { HeatmapDay } from "./types";

/** A poll day's YYYY-MM-DD key as "Tue 13 Oct", in Singapore time like the cells. */
export const dayLabel = (key: string) => dayText(startOfDay(key));

/** The time-of-day rows to draw, in order, across every day. */
export function rowTimes(days: { cells: { start_at: string }[] }[]): string[] {
  const seen = new Set<string>();
  for (const day of days) for (const cell of day.cells) seen.add(time(cell.start_at));
  return [...seen].sort();
}

/** free count by "HH:MM" for one day. */
export function dayLookup(day: HeatmapDay): Map<string, number> {
  return new Map(day.cells.map((c) => [time(c.start_at), c.free]));
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
