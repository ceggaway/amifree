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
        <Route path="/poll/:id" element={<p>Poll page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  saved.length = 0;
});

describe("MyTimes", () => {
  it("shows the capitalised event name and clear legend without extra controls", async () => {
    show({ ...grid, title: "dinner with friends", submitted: true });
    expect(await screen.findByRole("heading", { level: 1, name: "Dinner with friends" })).toBeTruthy();
    for (const label of ["Available", "Unavailable", "Calendar / class clash"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "Submit availabilities" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Drag to select|^all$|^none$/ })).toBeNull();
    expect(screen.queryByText(/half hours marked free/)).toBeNull();
    expect(screen.queryByText(/The group only sees/)).toBeNull();
  });

  it("blocks busy cells, lets free ones be unticked, and saves the ticked cells", async () => {
    show(grid);
    expect(await screen.findByText("Project meeting")).toBeTruthy();
    const busy = screen.getByLabelText(/08:30: busy/) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/09:00: free/));
    expect(screen.getByLabelText(/09:00: not free/).getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(screen.getByText("Submit availabilities"));
    await waitFor(() => expect(saved).toEqual([["2026-10-13T08:00:00+08:00"]]));
    // Saving counts the member in and returns to the poll.
    expect(await screen.findByText("Poll page")).toBeTruthy();
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
    expect(screen.getByLabelText(/09:00: not free/).getAttribute("aria-pressed")).toBe("false");
    expect((screen.getByLabelText(/08:30: busy/) as HTMLButtonElement).disabled).toBe(true);
    // The click that follows the pointer-up must not undo the paint.
    fireEvent.click(screen.getByLabelText(/09:00: not free/));
    expect(screen.getByLabelText(/09:00: not free/).getAttribute("aria-pressed")).toBe("false");
    // A later plain tap works again.
    fireEvent.click(screen.getByLabelText(/09:00: not free/));
    expect(screen.getByLabelText(/09:00: free/).getAttribute("aria-pressed")).toBe("true");
  });

  it("lets touch paint immediately without a toggle", async () => {
    show(grid);
    await screen.findByText("Project meeting");
    expect(screen.queryByRole("button", { name: /Drag to select/ })).toBeNull();
    fireEvent.pointerDown(screen.getByLabelText(/09:00: free/), { pointerType: "touch" });
    expect(screen.getByLabelText(/09:00: not free/).getAttribute("aria-pressed")).toBe("false");
    fireEvent.pointerUp(screen.getByLabelText(/09:00: not free/), { pointerType: "touch" });
    // Phones send the click after the pointer-up, sometimes late: it must not flip the cell back.
    await new Promise((resolve) => setTimeout(resolve, 20));
    fireEvent.click(screen.getByLabelText(/09:00: not free/));
    expect(screen.getByLabelText(/09:00: not free/).getAttribute("aria-pressed")).toBe("false");
  });

  it("paints by touch as the finger moves across cells", async () => {
    const wide: MyGrid = {
      ...grid,
      days: [
        {
          date: "2026-10-13",
          cells: [
            { start_at: "2026-10-13T09:00:00+08:00", state: "free" },
            { start_at: "2026-10-13T09:30:00+08:00", state: "free" },
          ],
        },
      ],
    };
    show(wide);
    await screen.findByText("Project meeting");
    expect(screen.getByText("Available")).toBeTruthy(); // the legend says what blue means
    const first = screen.getByLabelText(/09:00: free/);
    const second = screen.getByLabelText(/09:30: free/);
    fireEvent.pointerDown(first, { pointerType: "touch" });
    // Touch keeps reporting the first cell; the cell under the finger is looked up instead.
    const original = document.elementFromPoint;
    document.elementFromPoint = () => second;
    fireEvent.pointerMove(first, { pointerType: "touch", clientX: 10, clientY: 10 });
    document.elementFromPoint = original;
    fireEvent.pointerUp(first, { pointerType: "touch" });
    expect(screen.queryAllByRole("button", { pressed: true })).toHaveLength(0);
  });

  it("asks people with no schedule to tap the times they are free", async () => {
    show({ ...grid, known: false, days: [{ date: grid.days[0].date, cells: [{ start_at: "2026-10-13T08:00:00+08:00", state: "off" }] }] });
    expect(await screen.findByText(/Tap a slot or drag across several/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/08:00: not free/));
    expect(screen.getByLabelText(/08:00: free/).getAttribute("aria-pressed")).toBe("true");
  });

  it("says when the poll is closed or hidden", async () => {
    show({ ...grid, closed: true });
    expect(await screen.findByText("This poll is closed.")).toBeTruthy();
    cleanup();
    show(new Error("Not found"));
    expect(await screen.findByText("This poll isn't available to you.")).toBeTruthy();
  });
});

describe("MyTimes guidance", () => {
  const empty: MyGrid = {
    ...grid,
    days: [
      {
        date: "2026-10-13",
        cells: [
          { start_at: "2026-10-13T08:00:00+08:00", state: "off" },
          { start_at: "2026-10-13T08:30:00+08:00", state: "busy" },
          { start_at: "2026-10-13T09:00:00+08:00", state: "off" },
        ],
      },
    ],
  };

  it("marks a whole day free in one tap, skipping busy times, and keeps a running total", async () => {
    show(empty);
    expect(await screen.findByText("No free times marked yet")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Free all day on/ }));
    expect(screen.getByText("1 hr free")).toBeTruthy();
    expect(screen.getByLabelText(/08:30: busy/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Clear/ }));
    expect(screen.getByText("No free times marked yet")).toBeTruthy();
  });

  it("checks before submitting no free times at all", async () => {
    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    show(empty);
    fireEvent.click(await screen.findByText("Submit availabilities"));
    await waitFor(() => expect(ask).toHaveBeenCalled());
    expect(saved).toEqual([]);
    ask.mockRestore();
  });

  it("tells an organiser who just posted the poll why they're here", async () => {
    state.grid = grid;
    render(
      <MemoryRouter initialEntries={[{ pathname: "/poll/p1/mine", state: { justPosted: true } }]}>
        <Routes>
          <Route path="/poll/:id/mine" element={<MyTimes />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Poll posted in the group/)).toBeTruthy();
  });
});
