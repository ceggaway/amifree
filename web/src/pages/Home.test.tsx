import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../types";

const state: { me: Me | Error; saved: boolean[] } = { me: {} as Me, saved: [] };

vi.mock("../api", () => ({
  api: {
    me: () => (state.me instanceof Error ? Promise.reject(state.me) : Promise.resolve(state.me)),
    plans: () => Promise.resolve({ plans: [] }),
    setReminders: (on: boolean) => {
      state.saved.push(on);
      return Promise.resolve({ reminders_enabled: on });
    },
  },
}));

import { Home } from "./Home";

const base: Me = {
  telegram_user_id: 1,
  reminders_enabled: true,
  nusmods: { connected: true, academic_year: "2026-2027", semester: 1 },
  calendars: [],
  outside_calendars_connected: false,
};

async function summary(me: Me | Error) {
  state.me = me;
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
  // The summary appears once plans have loaded; /api/me fills in the sources.
  const heading = await screen.findByText(/No clashes in your upcoming plans/);
  await new Promise((resolve) => setTimeout(resolve, 0));
  return heading.parentElement!.textContent ?? "";
}

afterEach(cleanup);

describe("Home source summary", () => {
  it("says calendars are not connected when there are none", async () => {
    const text = await summary(base);
    expect(text).toContain("NUSMods timetable");
    expect(text).toContain("Outside calendars aren't connected");
  });

  it("reports a connected calendar, and does not claim none are connected", async () => {
    const text = await summary({
      ...base,
      outside_calendars_connected: true,
      calendars: [{ label: "CCA", synced_at: "2026-10-12T10:00:00+08:00", status: "CONNECTED" }],
    });
    expect(text).toContain("your connected calendar");
    expect(text).not.toContain("aren't connected");
  });

  it("flags a calendar link that stopped working", async () => {
    const text = await summary({
      ...base,
      calendars: [{ label: "CCA", synced_at: null, status: "ERROR" }],
    });
    expect(text).toContain("isn't working");
    expect(text).not.toContain("aren't connected");
  });

  it("warns when one of several connected calendars cannot be read", async () => {
    const text = await summary({
      ...base,
      outside_calendars_connected: true,
      calendars: [
        { label: "CCA", synced_at: "2026-10-12T10:00:00+08:00", status: "CONNECTED" },
        { label: "Events", synced_at: null, status: "ERROR" },
      ],
    });
    expect(text).toContain("your connected calendars");
    expect(text).toContain("1 can't be read");
  });

  it("asks to connect NUSMods when no timetable is imported", async () => {
    const text = await summary({ ...base, nusmods: { ...base.nusmods, connected: false } });
    expect(text).toContain("no NUSMods timetable");
  });
});

describe("Reminders setting", () => {
  it("turns reminders off and on from the home page", async () => {
    state.me = base;
    state.saved = [];
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );
    const toggle = await screen.findByRole("switch", { name: "🔔 Reminders" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));
    expect(screen.getByText(/Off: none of those/)).toBeTruthy();
    fireEvent.click(toggle);
    await waitFor(() => expect(state.saved).toEqual([false, true]));
  });
});
