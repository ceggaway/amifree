import type { CellState, MyGrid } from "./types";

/** The cells currently ticked, in grid order. */
export function ticked(grid: MyGrid): string[] {
  return grid.days.flatMap((d) => d.cells.filter((c) => c.state === "free").map((c) => c.start_at));
}

/** Tapping flips free and off; a blocked cell never changes. */
export function toggle(grid: MyGrid, start: string): MyGrid {
  return {
    ...grid,
    days: grid.days.map((day) => ({
      ...day,
      cells: day.cells.map((cell) =>
        cell.start_at === start && cell.state !== "busy"
          ? { ...cell, state: (cell.state === "free" ? "off" : "free") as CellState }
          : cell,
      ),
    })),
  };
}

/** Set every open cell of one day: used by "all day" and "clear day". */
export function setDay(grid: MyGrid, date: string, state: "free" | "off"): MyGrid {
  return {
    ...grid,
    days: grid.days.map((day) =>
      day.date === date
        ? { ...day, cells: day.cells.map((c) => (c.state === "busy" ? c : { ...c, state })) }
        : day,
    ),
  };
}
