import { describe, expect, it } from "vitest";
import { bestCell, dayLabel, dayLookup, rowTimes, shade } from "./heatmap";

// Cells arrive from the API in Singapore time, as the bot writes them.
const day = (date: string, frees: number[]) => ({
  date,
  cells: frees.map((free, i) => ({
    start_at: new Date(Date.UTC(2026, 9, 13, Math.floor(i / 2), (i % 2) * 30)).toISOString(),
    free,
  })),
});

describe("heatmap helpers", () => {
  it("lists each time of day once, in order", () => {
    expect(rowTimes([day("2026-10-13", [1, 2, 3]), day("2026-10-14", [1, 2])])).toEqual([
      "08:00",
      "08:30",
      "09:00",
    ]);
  });

  it("looks cells up by time of day", () => {
    expect(dayLookup(day("2026-10-13", [4, 5])).get("08:30")).toBe(5);
  });

  it("labels rows and days in Singapore time, whatever the phone's time zone", () => {
    expect(rowTimes([{ cells: [{ start_at: "2026-10-13T08:00:00+08:00" }] }])).toEqual(["08:00"]);
    expect(dayLabel("2026-10-13")).toMatch(/Tue.*13.*Oct/);
  });

  it("shades from nobody to everybody and survives an empty group", () => {
    expect(shade(0, 6)).toBe(0);
    expect(shade(3, 6)).toBe(0.5);
    expect(shade(9, 6)).toBe(1);
    expect(shade(2, 0)).toBe(0);
  });

  it("finds the cell with most people free", () => {
    expect(bestCell([day("a", [1, 5, 2]), day("b", [3])])?.free).toBe(5);
    expect(bestCell([])).toBeNull();
  });
});
