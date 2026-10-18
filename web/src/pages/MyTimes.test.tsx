import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MyGrid } from "../types";

// jsdom has no PointerEvent: give it one that keeps pointerType, so touch can be simulated.
class TestPointerEvent extends MouseEvent {
  pointerType: string;
  pointerId: number;
  constructor(type: string, init: MouseEventInit & { pointerType?: string; pointerId?: number } = {}) {
    super(type, init);
    this.pointerType = init.pointerType ?? "mouse";
    this.pointerId = init.pointerId ?? 1;
  }
}
(window as unknown as { PointerEvent: unknown }).PointerEvent = TestPointerEvent;

const saved: string[][] = [];
const grid: MyGrid = {
  poll_id: "p1",
  title: "Project meeting",
  duration_minutes: 60,
  closed: false,
  deadline_at: null,
  known: true,
  submitted: false,
  days: [
    {
      date: "2026-10-13",
      cells: [
        { start_at: "2026-10-13T08:00:00+08:00", state: "free" },
        { start_at: "2026-10-13T08:30:00+08:00", state: "busy" },
        { start_at: "2026-10-13T09:00:00+08:00", state: "free" },
      ],
    },
  ],
};
const state = { grid: grid as MyGrid | Error };

vi.mock("../api", () => ({
  api: {
    myGrid: () => (state.grid instanceof Error ? Promise.reject(state.grid) : Promise.resolve(state.grid)),
    saveGrid: (_id: string, free: string[]) => {
      saved.push(free);
      return Promise.resolve({ saved: free.length, dropped: 0 });
    },
  },
}));

import { MyTimes } from "./MyTimes";

function show(g: MyGrid | Error) {
  state.grid = g;
  render(
    <MemoryRouter initialEntries={["/poll/p1/mine"]}>
      <Routes>
        <Route path="/poll/:id/mine" element={<MyTimes />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  saved.length = 0;
});

describe("MyTimes", () => {
  it("blocks busy cells, lets free ones be unticked, and saves the ticked cells", async () => {
    show(grid);
    expect(await screen.findByText("Project meeting")).toBeTruthy();
    const busy = screen.getByLabelText(/08:30: busy/) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/09:00: free/));
    expect(screen.getByText("1 half hours ticked.")).toBeTruthy();
    fireEvent.click(screen.getByText("Submit my times"));
    await waitFor(() => expect(saved).toEqual([["2026-10-13T08:00:00+08:00"]]));
    expect(await screen.findByText(/Saved 1 half hours/)).toBeTruthy();
  });

  it("paints many cells with one drag, and never touches a busy cell", async () => {
    const wide: MyGrid = {
      ...grid,
      days: [
        {
          date: "2026-10-13",
          cells: [
            { start_at: "2026-10-13T08:00:00+08:00", state: "free" },
            { start_at: "2026-10-13T08:30:00+08:00", state: "busy" },
            { start_at: "2026-10-13T09:00:00+08:00", state: "free" },
            { start_at: "2026-10-13T09:30:00+08:00", state: "free" },
          ],
        },
      ],
    };
    show(wide);
    await screen.findByText("Project meeting");
    // Mouse: press on 09:00 (free, so the drag clears), move over 09:30 and the busy 08:30.
    fireEvent.pointerDown(screen.getByLabelText(/09:00: free/), { pointerType: "mouse" });
    fireEvent.pointerEnter(screen.getByLabelText(/09:30: free/), { pointerType: "mouse" });
    fireEvent.pointerEnter(screen.getByLabelText(/08:30: busy/), { pointerType: "mouse" });
    fireEvent.pointerUp(screen.getByLabelText(/09:30: not free/));
    expect(screen.getByText("1 half hours ticked.")).toBeTruthy();
    expect((screen.getByLabelText(/08:30: busy/) as HTMLButtonElement).disabled).toBe(true);
    // The click that follows the pointer-up must not undo the paint.
    fireEvent.click(screen.getByLabelText(/09:00: not free/));
    expect(screen.getByText("1 half hours ticked.")).toBeTruthy();
    // A later plain tap works again.
    fireEvent.click(screen.getByLabelText(/09:00: not free/));
    expect(screen.getByText("2 half hours ticked.")).toBeTruthy();
  });

  it("needs paint mode before a touch drag paints, but a touch tap always toggles", async () => {
    show(grid);
    await screen.findByText("Project meeting");
    fireEvent.pointerDown(screen.getByLabelText(/09:00: free/), { pointerType: "touch" });
    expect(screen.getByText("2 half hours ticked.")).toBeTruthy(); // swipe, not paint
    fireEvent.click(screen.getByLabelText(/09:00: free/));
    expect(screen.getByText("1 half hours ticked.")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Paint mode/));
    fireEvent.pointerDown(screen.getByLabelText(/08:00: free/), { pointerType: "touch" });
    expect(screen.getByText("0 half hours ticked.")).toBeTruthy();
  });

  it("asks people with no schedule to tap the times they are free", async () => {
    show({ ...grid, known: false, days: [{ date: grid.days[0].date, cells: [{ start_at: "2026-10-13T08:00:00+08:00", state: "off" }] }] });
    expect(await screen.findByText(/don't have your schedule yet/)).toBeTruthy();
  });

  it("says when the poll is closed or hidden", async () => {
    show({ ...grid, closed: true });
    expect(await screen.findByText("This poll is closed.")).toBeTruthy();
    cleanup();
    show(new Error("Not found"));
    expect(await screen.findByText("This poll isn't available to you.")).toBeTruthy();
  });
});
