import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupOverview, GroupPoll } from "../types";

const state: { group: GroupOverview; switched: boolean[] } = { group: {} as GroupOverview, switched: [] };

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    group: () => Promise.resolve(state.group),
    setDetection: (_code: string, on: boolean) => {
      state.switched.push(on);
      return Promise.resolve({ detection: on });
    },
  },
}));

import { GroupHome } from "./GroupHome";

const base: GroupOverview = { chat: "n100500", title: "CS3216", events: [], polls: [], admin: false, detection: false };

function show(group: GroupOverview) {
  state.group = group;
  state.switched = [];
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
  it("shows only unanswered availability requests and opens their availability form", async () => {
    const poll: GroupPoll = { id: "pending", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 2, joined_by_me: false, organiser: false, availability_submitted: false, deadline_at: null, min_joined: 3 };
    show({ ...base, polls: [
      poll,
      { ...poll, id: "answered", title: "Already answered", joined_by_me: true, availability_submitted: true },
      { ...poll, id: "results", title: "Results ready", status: "OPEN" },
    ] });
    expect(await screen.findByRole("heading", { name: "Pending action" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Project meeting/ }).getAttribute("href")).toBe("/poll/pending/mine");
    expect(screen.getByText("Add your availability ›")).toBeTruthy();
    expect(screen.queryByText("Already answered")).toBeNull();
    expect(screen.queryByText("Results ready")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Finding a time" })).toBeNull();
  });

  it("hides pending action once the member has submitted availability", async () => {
    show({ ...base, polls: [{ id: "answered", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 2, joined_by_me: true, organiser: false, availability_submitted: true, deadline_at: null, min_joined: 3 }] });
    await screen.findByText("Attendance and insights");
    expect(screen.queryByRole("heading", { name: "Pending action" })).toBeNull();
    expect(screen.queryByText("Project meeting")).toBeNull();
  });

  it("keeps ready results actionable for the organiser even after submitting availability", async () => {
    show({ ...base, polls: [{ id: "results", title: "Project meeting", status: "OPEN", duration_minutes: 60, joined: 3, joined_by_me: true, organiser: true, availability_submitted: true, deadline_at: null, min_joined: 3 }] });
    expect(await screen.findByRole("heading", { name: "Pending action" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Project meeting/ }).getAttribute("href")).toBe("/poll/results");
    expect(screen.getByText("Choose a time to create the event ›")).toBeTruthy();
  });

  it("still asks a new organiser to submit availability after starting a poll", async () => {
    show({ ...base, polls: [{ id: "started", title: "Project meeting", status: "COLLECTING", duration_minutes: 60, joined: 1, joined_by_me: true, organiser: true, availability_submitted: false, deadline_at: null, min_joined: 3 }] });
    expect(await screen.findByRole("heading", { name: "Pending action" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Project meeting/ }).getAttribute("href")).toBe("/poll/started/mine");
  });

  it("links to turnout for everyone, and hides detection from non-admins", async () => {
    show(base);
    expect(await screen.findByText("Attendance and insights")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("lets an admin switch plan spotting on", async () => {
    show({ ...base, admin: true });
    const toggle = await screen.findByRole("switch", { name: "Spot plans in chat" });
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
    expect(state.switched).toEqual([true]);
    expect(screen.getByText(/must be an admin of the group/)).toBeTruthy();
  });
});
