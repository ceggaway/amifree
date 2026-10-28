import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupOverview, GroupPoll } from "../types";

const state: { group: GroupOverview } = { group: {} as GroupOverview };

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    group: () => Promise.resolve(state.group),
    nudgePoll: () => Promise.resolve({ message: "Nudging 2 members privately." }),
    cancelPoll: (id: string) => {
      state.group = { ...state.group, polls: state.group.polls.filter((p) => p.id !== id) };
      return Promise.resolve({ message: "Poll cancelled." });
    },
  },
}));

import { GroupHome } from "./GroupHome";

const base: GroupOverview = { chat: "n100500", title: "CS3216", events: [], polls: [], admin: false };

function show(group: GroupOverview) {
  state.group = group;
  render(
    <MemoryRouter initialEntries={["/g/n100500"]}>
      <Routes>
        <Route path="/g/:code" element={<GroupHome />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("group page", () => {
  it("shows a capitalised pending title and deadline with a direct availability action", async () => {
    show({ ...base, polls: [{ id: "dinner", title: "dinner with friends", status: "COLLECTING", duration_minutes: 60, joined: 12, joined_by_me: false, organiser: false, availability_submitted: false, deadline_at: "2026-10-26T23:59:00+08:00", min_joined: 3 }] });
    const card = await screen.findByRole("article", { name: "Dinner with friends" });
    expect(within(card).getByText("Reply by Mon, 26 Oct")).toBeTruthy();
    expect(within(card).queryByText(/joined/)).toBeNull();
    expect(within(card).getByRole("link", { name: "Fill in my availabilities for Dinner with friends" }).getAttribute("href")).toBe("/poll/dinner/mine");
  });

  it("shows an upcoming date tile, title, time and details action without extra metadata", async () => {
    show({ ...base, events: [{ id: "dinner", title: "dinner", start_at: "2026-10-25T19:00:00+08:00", end_at: "2026-10-25T21:00:00+08:00", location: "Vivo", counts: { GOING: 3, DECLINED: 1 }, my_status: "GOING", recurring: true, organiser: true }] });
    const card = await screen.findByRole("article", { name: "Dinner" });
    expect(screen.getByRole("heading", { name: "Coming up" })).toBeTruthy();
    expect(within(card).getByText("Oct")).toBeTruthy();
    expect(within(card).getByText("25")).toBeTruthy();
    expect(within(card).getByText("Sunday")).toBeTruthy();
    expect(within(card).getByText("19:00–21:00")).toBeTruthy();
    expect(within(card).getByRole("link", { name: "Check details for Dinner" }).getAttribute("href")).toBe("/e/dinner");
    expect(within(card).queryByText(/going|organise|Vivo|repeats|Sun, 25 Oct/)).toBeNull();
    expect(screen.queryByText("1 event")).toBeNull();
  });

  it("shows only unanswered availability requests and opens their availability form", async () => {
    const poll: GroupPoll = { id: "pending", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 2, joined_by_me: false, organiser: false, availability_submitted: false, deadline_at: null, min_joined: 3 };
    show({ ...base, polls: [
      poll,
      { ...poll, id: "answered", title: "Already answered", joined_by_me: true, availability_submitted: true },
      { ...poll, id: "results", title: "Results ready", status: "OPEN" },
    ] });
    expect(await screen.findByRole("heading", { name: "Needs you" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Fill in my availabilities for Project meeting" }).getAttribute("href")).toBe("/poll/pending/mine");
    expect(screen.getByRole("link", { name: "Fill in my availabilities for Project meeting" })).toBeTruthy();
    expect(screen.queryByText("Already answered")).toBeNull();
    expect(screen.queryByText("Results ready")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Finding a time" })).toBeNull();
  });

  it("hides pending action once the member has submitted availability", async () => {
    show({ ...base, polls: [{ id: "answered", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 2, joined_by_me: true, organiser: false, availability_submitted: true, deadline_at: null, min_joined: 3 }] });
    await screen.findByText("Attendance history");
    expect(screen.queryByRole("heading", { name: "Needs you" })).toBeNull();
    expect(screen.queryByText("Project meeting")).toBeNull();
  });

  it("keeps ready results actionable for the organiser even after submitting availability", async () => {
    show({ ...base, polls: [{ id: "results", title: "Project meeting", status: "OPEN", duration_minutes: 60, joined: 3, joined_by_me: true, organiser: true, availability_submitted: true, deadline_at: null, min_joined: 3 }] });
    expect(await screen.findByRole("heading", { name: "Needs you" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Choose a time for Project meeting" }).getAttribute("href")).toBe("/poll/results");
    expect(screen.getByRole("link", { name: "Choose a time for Project meeting" })).toBeTruthy();
  });

  it("still asks a new organiser to submit availability after starting a poll", async () => {
    show({ ...base, polls: [{ id: "started", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 1, joined_by_me: true, organiser: true, availability_submitted: false, deadline_at: null, min_joined: 3 }] });
    expect(await screen.findByRole("heading", { name: "Needs you" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Fill in my availabilities for Project meeting" }).getAttribute("href")).toBe("/poll/started/mine");
  });

  it("links to turnout for everyone, and hides detection from non-admins", async () => {
    show(base);
    expect(await screen.findByText("Attendance history")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it.each([false, true])("does not expose plan spotting for admin=%s", async (admin) => {
    show({ ...base, admin });
    await screen.findByText("Attendance history");
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByText("Spot plans in chat")).toBeNull();
  });
});

describe("first visit", () => {
  it("asks whether the time is known, and shows a one-time intro", async () => {
    localStorage.clear();
    show(base);
    expect(await screen.findByText("Plan something")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Schedule an event/ }).getAttribute("href")).toBe("/g/n100500/new");
    expect(screen.getByRole("link", { name: /Find a time that works/ }).getAttribute("href")).toBe("/g/n100500/find");
    expect(screen.getByText(/Nothing planned yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByText("How it works")).toBeNull();
    cleanup();
    show(base);
    await screen.findByText("Plan something");
    expect(screen.queryByText("How it works")).toBeNull();
  });
});

describe("organiser poll progress", () => {
  it("keeps the organiser's poll on the page with progress and a reminder", async () => {
    show({ ...base, polls: [{ id: "mine", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 3, waiting: 2, joined_by_me: true, organiser: true, availability_submitted: true, deadline_at: null, min_joined: 3 }] });
    const card = await screen.findByRole("article", { name: "Project meeting" });
    expect(within(card).getByText(/3 filled in · 2 not yet/)).toBeTruthy();
    expect(within(card).getByRole("link", { name: "See who's filled in for Project meeting" }).getAttribute("href")).toBe("/poll/mine");
    fireEvent.click(within(card).getByRole("button", { name: "Remind the 2" }));
    expect(await within(card).findByText("Nudging 2 members privately.")).toBeTruthy();
  });
});

describe("cancelling a poll", () => {
  it("lets the organiser cancel from the card, after confirming", async () => {
    const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
    show({ ...base, polls: [{ id: "mine", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 1, waiting: 2, joined_by_me: true, organiser: true, availability_submitted: true, deadline_at: null, min_joined: 3 }] });
    const card = await screen.findByRole("article", { name: "Project meeting" });
    fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("Project meeting")).toBeNull());
    expect(ask).toHaveBeenCalled();
    ask.mockRestore();
  });

  it("shows no Cancel to members", async () => {
    show({ ...base, polls: [{ id: "theirs", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 1, joined_by_me: false, organiser: false, availability_submitted: false, deadline_at: null, min_joined: 3 }] });
    const card = await screen.findByRole("article", { name: "Project meeting" });
    expect(within(card).queryByRole("button", { name: "Cancel" })).toBeNull();
  });
});
