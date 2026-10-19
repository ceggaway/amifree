import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PollDetail } from "../types";

const calls: unknown[][] = [];
const state: { poll: PollDetail } = { poll: {} as PollDetail };

vi.mock("../api", async () => {
  const { ApiError } = await vi.importActual<typeof import("../api")>("../api");
  return {
    ApiError,
    refusal: (e: unknown) => (e instanceof ApiError ? e.detail : { message: String(e) }),
    api: {
      poll: () => Promise.resolve(state.poll),
      joinPoll: () => {
        calls.push(["join"]);
        return Promise.resolve({ ...state.poll, joined: state.poll.joined + 1, joined_by_me: true });
      },
      vote: (_id: string, slot: number, answer: string) => {
        calls.push(["vote", slot, answer]);
        return Promise.resolve({ ...state.poll, mine: { [String(slot)]: answer } });
      },
      pick: (_id: string, slot: number) => {
        calls.push(["pick", slot]);
        return Promise.resolve({ event_id: "e1" });
      },
      pollHeatmap: () => new Promise(() => {}),
    },
  };
});

import { Poll } from "./Poll";

const slot = (day: number) => ({
  start_at: `2026-10-2${day}T14:00:00+08:00`,
  end_at: `2026-10-2${day}T16:00:00+08:00`,
  free: 4,
  of: 5,
  yes: 2,
  maybe: 0,
});
const base: PollDetail = {
  id: "p1",
  chat: "n100500",
  title: "Project meeting",
  status: "OPEN",
  duration_minutes: 120,
  deadline_at: null,
  joined: 5,
  joined_by_me: false,
  min_joined: 3,
  slots: [slot(1), slot(2), slot(3)],
  mine: {},
  organiser: false,
};

function show(poll: PollDetail) {
  state.poll = poll;
  render(
    <MemoryRouter initialEntries={["/poll/p1"]}>
      <Routes>
        <Route path="/poll/:id" element={<Poll />} />
        <Route path="/e/:id" element={<p>Event page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("poll page", () => {
  it("answers each time with Yes, Maybe or No", async () => {
    show(base);
    const [, second] = await screen.findAllByRole("group");
    fireEvent.click(second.querySelector("button:nth-child(2)")!);
    await waitFor(() => expect(calls).toEqual([["vote", 1, "MAYBE"]]));
    expect(second.querySelector("button:nth-child(2)")!.getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText("Pick this time")).toBeNull();
  });

  it("lets a member count themselves in while times are being collected", async () => {
    show({ ...base, status: "COLLECTING", slots: [], joined: 2 });
    fireEvent.click(await screen.findByRole("button", { name: "Count me in" }));
    expect(await screen.findByText("You're in.")).toBeTruthy();
    expect(calls).toEqual([["join"]]);
  });

  it("lets the organiser pick a time, which opens the posted event", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    show({ ...base, organiser: true });
    const picks = await screen.findAllByRole("button", { name: "Pick this time" });
    fireEvent.click(picks[2]);
    expect(await screen.findByText("Event page")).toBeTruthy();
    expect(calls).toEqual([["pick", 2]]);
  });
});
