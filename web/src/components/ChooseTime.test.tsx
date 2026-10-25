import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Heatmap } from "../types";

const state: { data: Heatmap } = { data: {} as Heatmap };
const picks: string[] = [];

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    pollHeatmap: () => Promise.resolve(state.data),
    pickTime: (_id: string, start: string) => {
      picks.push(start);
      return Promise.resolve({ event_id: "e1" });
    },
  },
}));

vi.mock("../telegram", () => ({ confirm: () => Promise.resolve(true), haptic: () => {} }));

import { ChooseTime } from "./ChooseTime";

const cells = (date: string, counts: number[]) =>
  counts.map((free, i) => ({
    start_at: `${date}T${String(8 + Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}:00+08:00`,
    free,
  }));

const base: Heatmap = {
  poll_id: "p1",
  title: "Sync",
  duration_minutes: 60,
  joined: 2,
  known: 2,
  min_known: 2,
  ready: true,
  max_free: 2,
  status: "OPEN",
  organiser: true,
  days: [
    { date: "2026-10-13", cells: cells("2026-10-13", [2, 1, 2]) },
    { date: "2026-10-14", cells: cells("2026-10-14", [2, 2]) },
  ],
};

function show(data: Heatmap) {
  state.data = data;
  render(
    <MemoryRouter initialEntries={["/poll/p1"]}>
      <Routes>
        <Route path="/poll/:id" element={<ChooseTime pollId="p1" />} />
        <Route path="/e/:id" element={<p>Event page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  picks.length = 0;
});

describe("ChooseTime", () => {
  it("lists only start times where the whole meeting fits, with how many are free", async () => {
    show(base);
    const start = (await screen.findByLabelText("Start time")) as HTMLSelectElement;
    // A 1-hour meeting: 08:00 (lowest of 2 and 1) and 08:30 fit; 09:00 would run past the day.
    expect([...start.options].map((o) => o.text)).toEqual([
      "08:00–09:00 (1 of 2 available)",
      "08:30–09:30 (1 of 2 available)",
    ]);
  });

  it("confirms the chosen day and time", async () => {
    show(base);
    fireEvent.change(await screen.findByLabelText("Day"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm this time" }));
    await waitFor(() => expect(picks).toEqual(["2026-10-14T08:00:00+08:00"]));
    expect(await screen.findByText("Event page")).toBeTruthy();
  });

  it("waits until enough people have filled in", async () => {
    show({ ...base, ready: false, known: 1, days: [] });
    expect(await screen.findByText(/Once at least 2 people/)).toBeTruthy();
  });
});
