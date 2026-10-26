import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupOverview, GroupPoll } from "../types";

const state: { group: GroupOverview } = { group: {} as GroupOverview };

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    group: () => Promise.resolve(state.group),
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
    expect(within(card).getByRole("link", { name: "Add your availability for Dinner with friends" }).getAttribute("href")).toBe("/poll/dinner/mine");
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
    expect(screen.getByRole("link", { name: "Add your availability for Project meeting" }).getAttribute("href")).toBe("/poll/pending/mine");
    expect(screen.getByRole("link", { name: "Add your availability for Project meeting" })).toBeTruthy();
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
    expect(screen.getByRole("link", { name: "Add your availability for Project meeting" }).getAttribute("href")).toBe("/poll/started/mine");
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
    expect(await screen.findByText("Planning something? Do you know when?")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Yes, set the time/ }).getAttribute("href")).toBe("/g/n100500/new");
    expect(screen.getByRole("link", { name: /No, help us pick a time/ }).getAttribute("href")).toBe("/g/n100500/find");
    expect(screen.getByText(/Nothing planned yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByText("How it works")).toBeNull();
    cleanup();
    show(base);
    await screen.findByText("Planning something? Do you know when?");
    expect(screen.queryByText("How it works")).toBeNull();
  });
});
