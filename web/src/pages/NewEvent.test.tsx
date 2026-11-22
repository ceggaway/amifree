import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Availability, EventForm, Me } from "../types";

const sent: EventForm[] = [];
const profile: Me = {
  telegram_user_id: 1,
  reminders_enabled: true,
  nusmods: { connected: false, academic_year: null, semester: null },
  calendars: [],
  outside_calendars_connected: false,
};
const available: Availability = { verdict: "FREE", covered: true, busy_day: false, day_count: 0, conflicts: [], possible: [] };
const state = { me: profile, availability: available };
const checkAvailability = vi.fn(() => Promise.resolve(state.availability));

vi.mock("../api", () => ({
  refusal: () => ({ message: "No" }),
  api: {
    me: () => Promise.resolve(state.me),
    availability: () => checkAvailability(),
    createEvent: (_code: string, form: EventForm) => {
      sent.push(form);
      return Promise.resolve({ id: "e9" });
    },
  },
}));

import { NewEvent } from "./NewEvent";

beforeEach(() => {
  // The form rejects past dates. Keep these fixtures in the future as real time advances,
  // while leaving timers real for the availability check and Testing Library waits.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-19T12:00:00+08:00"));
  sent.length = 0;
  state.me = profile;
  state.availability = available;
  checkAvailability.mockClear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("new event form", () => {
  it("labels the event name and offers setup without claiming a clash check for unconnected users", async () => {
    render(<MemoryRouter><NewEvent /></MemoryRouter>);
    expect(screen.getByLabelText("Name of event")).toBeTruthy();
    expect(await screen.findByText("Connect your personal calendar or add your NUSMods timetable to check this event against your schedule automatically.")).toBeTruthy();
    expect(screen.queryByText(/No clashes/)).toBeNull();
    expect(checkAvailability).not.toHaveBeenCalled();
  });

  it.each(["calendar", "timetable"])("checks the user's own schedule when a %s is connected", async (source) => {
    state.me = source === "timetable"
      ? { ...profile, nusmods: { ...profile.nusmods, connected: true } }
      : { ...profile, outside_calendars_connected: true };
    render(<MemoryRouter><NewEvent /></MemoryRouter>);
    expect(await screen.findByText("No clashes with your schedule.")).toBeTruthy();
    expect(screen.queryByText(/Connect your personal calendar or add your NUSMods timetable/)).toBeNull();
    expect(checkAvailability).toHaveBeenCalled();
  });

  it("keeps the overlapping commitment and time visible for a connected user", async () => {
    state.me = { ...profile, outside_calendars_connected: true };
    state.availability = { ...available, verdict: "CLASH", conflicts: [{ title: "Project meeting", start_at: "2026-10-20T19:00:00+08:00", end_at: "2026-10-20T20:00:00+08:00", location: null, source: "CALENDAR" }] };
    render(<MemoryRouter><NewEvent /></MemoryRouter>);
    expect(await screen.findByText(/Clashes with Project meeting \(19:00–20:00\)/)).toBeTruthy();
    expect(screen.queryByText("No clashes with your schedule.")).toBeNull();
  });

  it("posts a weekly event in Singapore time and opens it", async () => {
    render(
      <MemoryRouter initialEntries={["/g/n100500/new"]}>
        <Routes>
          <Route path="/g/:code/new" element={<NewEvent />} />
          <Route path="/e/:id" element={<p>Event page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByPlaceholderText("Dinner"), { target: { value: "Training" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-10-20" } });
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "19:00" } });
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "21:00" } });
    fireEvent.click(screen.getByLabelText(/Repeats every week/));
    fireEvent.click(screen.getByRole("button", { name: "Thu" })); // Tue (the date) and Thu
    fireEvent.click(screen.getByRole("button", { name: "Tue" }));
    fireEvent.click(screen.getByRole("button", { name: "Post to group" }));
    expect(await screen.findByText("Event page")).toBeTruthy();
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      title: "Training",
      start_at: "2026-10-20T19:00:00+08:00",
      end_at: "2026-10-20T21:00:00+08:00",
      weekdays: [3, 1],
    });
    expect(sent[0].request_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("runs past midnight when the end is before the start", async () => {
    render(
      <MemoryRouter initialEntries={["/g/n100500/new"]}>
        <Routes>
          <Route path="/g/:code/new" element={<NewEvent />} />
          <Route path="/e/:id" element={<p>Event page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByPlaceholderText("Dinner"), { target: { value: "Supper" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-10-20" } });
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "23:00" } });
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "01:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Post to group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].end_at).toBe("2026-10-21T01:00:00+08:00");
  });
});
