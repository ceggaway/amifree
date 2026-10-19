import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupStats as Stats } from "../types";

const calls: unknown[][] = [];
const state: { stats: Stats } = { stats: {} as Stats };

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    groupStats: (_code: string, days: number) => {
      calls.push(["stats", days]);
      return Promise.resolve(state.stats);
    },
    exportAttendance: (_code: string, days: number) => {
      calls.push(["export", days]);
      return Promise.resolve({ message: "📬 Sent the attendance file to your chat with AmIFree." });
    },
  },
}));

import { GroupStats } from "./GroupStats";

const stats: Stats = {
  days: 120,
  held: 3,
  cancelled: 0,
  going: 20,
  came: 15,
  turnout: "75%",
  by_event: [{ title: "Training", sessions: 3, going: 20, came: 15, turnout: "75%" }],
  pro_required: false,
};

function show(value: Stats) {
  state.stats = value;
  render(
    <MemoryRouter initialEntries={["/g/n100500/stats"]}>
      <Routes>
        <Route path="/g/:code/stats" element={<GroupStats />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("turnout page", () => {
  it("shows turnout, changes the period and sends the CSV", async () => {
    show(stats);
    expect(await screen.findByText("Training")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "1 year" }));
    await waitFor(() => expect(calls).toContainEqual(["stats", 365]));
    fireEvent.click(screen.getByRole("button", { name: "📄 Send CSV to my chat" }));
    expect(await screen.findByText(/Sent the attendance file/)).toBeTruthy();
    expect(calls).toContainEqual(["export", 365]);
  });

  it("locks the CSV when Clubs Pro is required", async () => {
    show({ ...stats, by_event: [], pro_required: true });
    expect(await screen.findByText(/part of Clubs Pro/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "📄 Send CSV to my chat" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
