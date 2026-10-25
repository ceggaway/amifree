import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Heatmap as HeatmapData } from "../types";

const state: { data: HeatmapData | Error } = { data: {} as HeatmapData };
const picks: string[] = [];

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    pollHeatmap: () =>
      state.data instanceof Error ? Promise.reject(state.data) : Promise.resolve(state.data),
    pickTime: (_id: string, start: string) => {
      picks.push(start);
      return Promise.resolve({ event_id: "e1" });
    },
  },
}));

vi.mock("../telegram", () => ({ confirm: () => Promise.resolve(true), haptic: () => {} }));

import { Heatmap } from "./Heatmap";

const base: HeatmapData = {
  poll_id: "p1",
  title: "Project meeting",
  duration_minutes: 120,
  joined: 4,
  known: 4,
  min_known: 2,
  ready: true,
  max_free: 4,
  status: "OPEN",
  organiser: false,
  days: [
    {
      date: "2026-10-13",
      cells: [
        { start_at: "2026-10-13T08:00:00+08:00", free: 1 },
        { start_at: "2026-10-13T08:30:00+08:00", free: 4 },
        { start_at: "2026-10-13T09:00:00+08:00", free: 4 },
        { start_at: "2026-10-13T09:30:00+08:00", free: 3 },
        { start_at: "2026-10-13T10:00:00+08:00", free: 4 },
      ],
    },
  ],
};

function show(data: HeatmapData | Error) {
  state.data = data;
  render(
    <MemoryRouter initialEntries={["/poll/p1"]}>
      <Routes>
        <Route path="/poll/:id" element={<Heatmap />} />
        <Route path="/e/:id" element={<p>Event page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  picks.length = 0;
});

describe("Heatmap", () => {
  it("shows counts per half hour and the best slot, never names", async () => {
    show(base);
    expect(await screen.findByText("Project meeting")).toBeTruthy();
    expect(screen.getByLabelText(/08:30: 4 of 4 free/)).toBeTruthy();
    expect(screen.getByText(/Most free: 4 of 4/)).toBeTruthy();
  });

  it("holds the grid back until enough schedules are known", async () => {
    show({ ...base, ready: false, known: 2, days: [] });
    expect(await screen.findByText(/2 of 4 members have a schedule/)).toBeTruthy();
    expect(screen.queryByRole("grid")).toBeNull();
  });

  it("shows the error when the poll isn't theirs", async () => {
    show(new Error("Not found"));
    expect(await screen.findByText(/Fill in your availabilities on the poll first/)).toBeTruthy();
  });

  it("shows members the count for the whole meeting, with nothing to choose", async () => {
    show(base);
    fireEvent.click(await screen.findByLabelText(/08:30: 4 of 4 free/));
    // A 2-hour meeting from 08:30 covers 08:30 to 10:30; the lowest count is 3.
    expect(screen.getByText(/3 of 4 free for the whole meeting/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Choose this time" })).toBeNull();
    fireEvent.click(screen.getByLabelText(/09:30: 3 of 4 free/));
    expect(screen.getByText(/would run past the last time/)).toBeTruthy();
  });

  it("lets the organiser choose any time, which opens the posted event", async () => {
    show({ ...base, organiser: true });
    expect(await screen.findByText(/Tap any time to choose it/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/08:00: 1 of 4 free/));
    expect(screen.getByText(/1 of 4 free for the whole meeting/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Choose this time" }));
    await waitFor(() => expect(picks).toEqual(["2026-10-13T08:00:00+08:00"]));
    expect(await screen.findByText("Event page")).toBeTruthy();
  });
});
