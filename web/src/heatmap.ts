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

/**
 * How many are free for a whole meeting starting at `startAt`: the lowest count across the half
 * hours it covers. Null when the meeting would run past the day's last cell.
 */
export function meetingFree(day: HeatmapDay, startAt: string, minutes: number): number | null {
  const first = day.cells.findIndex((c) => c.start_at === startAt);
  if (first < 0) return null;
  const begin = Date.parse(startAt);
  let lowest = Infinity;
  for (let step = 0; step < Math.ceil(minutes / 30); step++) {
    const cell = day.cells[first + step];
    if (!cell || Date.parse(cell.start_at) !== begin + step * 30 * 60 * 1000) return null;
    lowest = Math.min(lowest, cell.free);
  }
  return lowest;
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
