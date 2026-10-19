import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventForm } from "../types";

const sent: EventForm[] = [];

vi.mock("../api", () => ({
  refusal: () => ({ message: "No" }),
  api: {
    availability: () => Promise.resolve({ verdict: "FREE", covered: true, busy_day: false, day_count: 0, conflicts: [], possible: [] }),
    createEvent: (_code: string, form: EventForm) => {
      sent.push(form);
      return Promise.resolve({ id: "e9" });
    },
  },
}));

import { NewEvent } from "./NewEvent";

afterEach(cleanup);

describe("new event form", () => {
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
    fireEvent.click(screen.getByRole("button", { name: "Post to the group" }));
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
    sent.length = 0;
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
    fireEvent.click(screen.getByRole("button", { name: "Post to the group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].end_at).toBe("2026-10-21T01:00:00+08:00");
  });
});
