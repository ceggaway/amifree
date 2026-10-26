import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PollDetail } from "../types";

const calls: unknown[][] = [];
const state: { poll: PollDetail; refuse?: string } = { poll: {} as PollDetail };

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
      suggest: () => {
        calls.push(["suggest"]);
        return state.refuse
          ? Promise.reject(new ApiError(409, { message: state.refuse }))
          : Promise.resolve({ message: "Results are ready. Choose a time below." });
      },
      nudgePoll: () => Promise.resolve({ message: "Nudging 1 member privately." }),
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
    expect(screen.queryByRole("button", { name: "Choose" })).toBeNull();
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
    expect(await screen.findByText(/You're in\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Update my availabilities" })).toBeTruthy();
  });

  it("lets the organiser choose a time, which opens the posted event", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    show({ ...base, organiser: true });
    const picks = await screen.findAllByRole("button", { name: "Choose" });
    fireEvent.click(picks[2]);
    expect(await screen.findByText("Event page")).toBeTruthy();
    expect(calls).toEqual([["pick", 2]]);
  });

  it("does not create an event when the organiser cancels confirmation", async () => {
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    show({ ...base, organiser: true });
    fireEvent.click((await screen.findAllByRole("button", { name: "Choose" }))[0]);
    expect(confirmation).toHaveBeenCalledWith("Create an event at this time? This closes the poll and posts the event in the group for RSVPs.");
    expect(calls).toEqual([]);
    expect(screen.queryByText("Event page")).toBeNull();
  });
});

describe("poll progress", () => {
  it("shows the step and tells a new member what to do", async () => {
    show({ ...base, status: "COLLECTING", slots: [], joined: 1 });
    const current = await screen.findByText("Fill in");
    expect(current.closest("li")?.getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("Mark when you're free.")).toBeTruthy();
  });

  it("tells a member who filled in that they're done", async () => {
    show({ ...base, status: "COLLECTING", slots: [], joined_by_me: true });
    expect(await screen.findByText(/Nothing else to do/)).toBeTruthy();
  });

  it("tells the organiser to pick once results are in", async () => {
    show({ ...base, organiser: true });
    expect(await screen.findByText("Pick a time below.")).toBeTruthy();
    expect(screen.getByText("Best times").closest("li")?.getAttribute("aria-current")).toBe("step");
  });
});

describe("organiser progress", () => {
  it("lists who has filled in and who hasn't, only for the organiser", async () => {
    show({ ...base, status: "COLLECTING", slots: [], organiser: true, joined_by_me: true, people: { done: ["Angel", "Ben"], waiting: ["Chloe"] } });
    expect(await screen.findByText("Filled in (2)")).toBeTruthy();
    expect(screen.getByText("Angel, Ben")).toBeTruthy();
    expect(screen.getByText("Not yet (1)")).toBeTruthy();
    expect(screen.getByText("Chloe")).toBeTruthy();
    cleanup();
    show({ ...base, status: "COLLECTING", slots: [], people: null });
    await screen.findByText("Mark when you're free.");
    expect(screen.queryByText(/Filled in \(/)).toBeNull();
  });
});

describe("organiser buttons", () => {
  const collecting = { ...base, status: "COLLECTING" as const, slots: [], organiser: true, joined_by_me: true, people: { done: ["A"], waiting: ["B"] } };

  it("greys out See best times until enough people have filled in", async () => {
    show({ ...collecting, joined: 2 });
    expect(((await screen.findByRole("button", { name: "See best times now" })) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Best times need 3 people. 2 so far.")).toBeTruthy();
  });

  it("shows the result next to the buttons, including a refusal", async () => {
    window.scrollTo = () => {};
    state.refuse = "3 joined, but only 2 have a schedule AmIFree can read.";
    show({ ...collecting, joined: 3 });
    fireEvent.click(await screen.findByRole("button", { name: "See best times now" }));
    expect((await screen.findByRole("status")).textContent).toContain("only 2 have a schedule");
    fireEvent.click(screen.getByRole("button", { name: "Remind the rest" }));
    expect(await screen.findByText("Nudging 1 member privately.")).toBeTruthy();
    state.refuse = undefined;
  });
});
