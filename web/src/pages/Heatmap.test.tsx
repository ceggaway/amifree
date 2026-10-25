import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Heatmap as HeatmapData } from "../types";

const state: { data: HeatmapData | Error } = { data: {} as HeatmapData };

vi.mock("../api", () => ({
  api: {
    pollHeatmap: () =>
      state.data instanceof Error ? Promise.reject(state.data) : Promise.resolve(state.data),
  },
}));

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
  days: [
    {
      date: "2026-10-13",
      cells: [
        { start_at: "2026-10-13T08:00:00+08:00", free: 1 },
        { start_at: "2026-10-13T08:30:00+08:00", free: 4 },
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
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

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
});
