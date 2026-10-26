import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupEventDetail } from "../types";

const calls: unknown[][] = [];
const state: { event: GroupEventDetail; clashOnce: boolean } = { event: {} as GroupEventDetail, clashOnce: false };

vi.mock("../api", async () => {
  const { ApiError } = await vi.importActual<typeof import("../api")>("../api");
  return {
    ApiError,
    refusal: (e: unknown) => (e instanceof ApiError ? e.detail : { message: String(e) }),
    api: {
      groupEvent: () => Promise.resolve(state.event),
      rsvp: (_id: string, status: string, force: boolean) => {
        calls.push(["rsvp", status, force]);
        if (state.clashOnce && !force) {
          state.clashOnce = false;
          return Promise.reject(new ApiError(409, { message: "Clash", reason: "clash" }));
        }
        return Promise.resolve({ ...state.event, my_status: status, counts: { ...state.event.counts, GOING: 1 }, going: ["Ceggaway"] });
      },
      markAttendance: (_id: string, user: string | null, present: boolean) => {
        calls.push(["mark", user, present]);
        return Promise.resolve(state.event);
      },
    },
  };
});

import { GroupEvent } from "./GroupEvent";

const free = { verdict: "FREE", covered: true, busy_day: false, day_count: 0, conflicts: [], possible: [] } as const;
const base: GroupEventDetail = {
  id: "e1",
  chat: "n100500",
  title: "Dinner",
  start_at: "2026-10-23T19:00:00+08:00",
  end_at: "2026-10-23T21:00:00+08:00",
  location: "Vivo",
  notes: null,
  status: "ACTIVE",
  version: 1,
  ended: false,
  recurring: false,
  counts: { GOING: 0, DECLINED: 0 },
  my_status: null,
  availability: { ...free, conflicts: [] , possible: [] },
  going: [],
  checked_in: false,
  check_in_open: false,
  organiser: false,
};

function show(event: GroupEventDetail) {
  state.event = event;
  render(
    <MemoryRouter initialEntries={["/e/e1"]}>
      <Routes>
        <Route path="/e/:id" element={<GroupEvent />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("group event page", () => {
  it("answers, and asks before Going over a clash", async () => {
    state.clashOnce = true;
    show(base);
    fireEvent.click(await screen.findByRole("button", { name: "Going" }));
    fireEvent.click(await screen.findByRole("button", { name: "Going anyway" }));
    await waitFor(() => expect(screen.getByText("Ceggaway")).toBeTruthy());
    expect(calls).toEqual([
      ["rsvp", "GOING", false],
      ["rsvp", "GOING", true],
    ]);
    expect(screen.getByRole("button", { name: "Going" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows organiser tools only to the organiser", async () => {
    show(base);
    await screen.findByText("Dinner");
    expect(screen.queryByText("You're the organiser")).toBeNull();
    cleanup();
    show({
      ...base,
      organiser: true,
      manage: {
        summary: { going: 1, confirmed: 0, unsure: 0, dropped: 0, no_reply: 1 },
        came: 0,
        people: [{ id: "u2", name: "Angel", answer: "GOING", present: false }],
      },
    });
    expect(await screen.findByText("You're the organiser")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Angel/));
    await waitFor(() => expect(calls).toEqual([["mark", "u2", true]]));
  });

  it("offers no answers once an event is cancelled", async () => {
    show({ ...base, status: "CANCELLED" });
    expect(await screen.findByText(/was cancelled/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Going" })).toBeNull();
  });
});

describe("organiser tools by timing", () => {
  it("keeps attendance tools out of the way until the event is close", async () => {
    show({
      ...base,
      start_at: "2030-01-01T19:00:00+08:00",
      end_at: "2030-01-01T21:00:00+08:00",
      organiser: true,
      manage: {
        summary: { going: 2, confirmed: 0, unsure: 0, dropped: 0, no_reply: 2 },
        came: 0,
        people: [{ id: "u2", name: "Angel", answer: "GOING", present: false }],
      },
    });
    expect(await screen.findByText("You're the organiser")).toBeTruthy();
    expect(screen.queryByText(/Who came/)).toBeNull();
    expect(screen.queryByText("Still coming?")).toBeNull();
    expect(screen.getByRole("button", { name: "Remind the rest" })).toBeTruthy();
  });

  it("asks Are you going? before showing the private clash check", async () => {
    show({ ...base, start_at: "2030-01-01T19:00:00+08:00", end_at: "2030-01-01T21:00:00+08:00" });
    const question = await screen.findByRole("heading", { name: "Are you going?" });
    const privacy = screen.getByText(/No clashes/);
    expect(question.compareDocumentPosition(privacy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
