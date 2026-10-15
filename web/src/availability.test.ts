import { describe, expect, it } from "vitest";
import { dragState, paint, setDay, ticked, toggle } from "./availability";
import type { MyGrid } from "./types";

const grid: MyGrid = {
  poll_id: "p",
  title: "t",
  duration_minutes: 60,
  closed: false,
  deadline_at: null,
  known: true,
  submitted: false,
  days: [
    {
      date: "2026-10-13",
      cells: [
        { start_at: "a", state: "free" },
        { start_at: "b", state: "busy" },
        { start_at: "c", state: "off" },
      ],
    },
  ],
};

describe("availability helpers", () => {
  it("lists ticked cells in order", () => {
    expect(ticked(grid)).toEqual(["a"]);
  });
  it("flips free and off but never a busy cell", () => {
    expect(ticked(toggle(grid, "c"))).toEqual(["a", "c"]);
    expect(ticked(toggle(grid, "a"))).toEqual([]);
    expect(toggle(grid, "b")).toEqual(grid);
  });
  it("sets a whole day without touching busy cells", () => {
    expect(ticked(setDay(grid, "2026-10-13", "free"))).toEqual(["a", "c"]);
    expect(ticked(setDay(grid, "2026-10-13", "off"))).toEqual([]);
    expect(ticked(setDay(grid, "2000-01-01", "free"))).toEqual(["a"]);
  });
});

describe("painting", () => {
  it("paints chosen cells but never a busy one", () => {
    expect(ticked(paint(grid, new Set(["b", "c"]), "free"))).toEqual(["a", "c"]);
    expect(ticked(paint(grid, new Set(["a"]), "off"))).toEqual([]);
  });
  it("a drag paints the opposite of the cell it starts on", () => {
    expect(dragState("free")).toBe("off");
    expect(dragState("off")).toBe("free");
    expect(dragState("busy")).toBeNull();
  });
});
