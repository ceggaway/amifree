import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
        // The server keeps one vote each: voting for a time clears the others.
        state.poll = { ...state.poll, mine: answer === "FREE" ? { [String(slot)]: "FREE" } : {} };
        return Promise.resolve(state.poll);
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
        <Route path="/poll/:id/mine" element={<p>My times page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("poll page", () => {
  it("shows members the results while the organiser chooses", async () => {
    show(base);
    expect(await screen.findByText(/The organiser is choosing the time/)).toBeTruthy();
    expect(screen.getAllByText("4 of 5 free")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Create event at this time" })).toBeNull();
    expect(screen.queryByText("Vote for this time")).toBeNull();
  });

  it("sends a member to fill in their times while times are being collected", async () => {
    show({ ...base, status: "COLLECTING", slots: [], joined: 2 });
    expect(screen.queryByRole("button", { name: "Count me in" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Fill in my availabilities" }));
    expect(await screen.findByText("My times page")).toBeTruthy();
    expect(calls).toEqual([]);
  });

  it("shows a member who already filled in that they're in", async () => {
    show({ ...base, status: "COLLECTING", slots: [], joined: 3, joined_by_me: true });
    expect(await screen.findByText("You're in.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Update my availabilities" })).toBeTruthy();
  });

  it("lets the organiser choose a time, which opens the posted event", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    show({ ...base, organiser: true });
    const picks = await screen.findAllByRole("button", { name: "Create event at this time" });
    fireEvent.click(picks[2]);
    expect(await screen.findByText("Event page")).toBeTruthy();
    expect(calls).toEqual([["pick", 2]]);
  });

  it("does not create an event when the organiser cancels confirmation", async () => {
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    show({ ...base, organiser: true });
    fireEvent.click((await screen.findAllByRole("button", { name: "Create event at this time" }))[0]);
    expect(confirmation).toHaveBeenCalledWith("Create an event at this time? This closes the poll and posts the event in the group for RSVPs.");
    expect(calls).toEqual([]);
    expect(screen.queryByText("Event page")).toBeNull();
  });
});
