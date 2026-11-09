import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dayLabel } from "../heatmap";
import { shiftKey, todayKey } from "../format";

const sent: Record<string, unknown>[] = [];

vi.mock("../api", () => ({
  refusal: (e: unknown) => ({ message: String(e) }),
  api: {
    createPoll: (_code: string, form: Record<string, unknown>) => {
      sent.push(form);
      return Promise.resolve({ id: "p1" });
    },
  },
}));

import { FindTime } from "./FindTime";

function show() {
  render(
    <MemoryRouter initialEntries={["/g/abc/find"]}>
      <Routes>
        <Route path="/g/:code/find" element={<FindTime />} />
        <Route path="/poll/:id/mine" element={<p>My times page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  sent.length = 0;
});

describe("FindTime", () => {
  it("asks for the days and an optional reply-by date", () => {
    show();
    const durations = screen.getByRole("group", { name: "Estimated time" });
    expect(within(durations).getAllByRole("button").map((button) => button.textContent)).toEqual(["30 min", "1 hour", "1.5 hours", "2 hours", "Custom"]);
    expect(screen.getByText("Which days?")).toBeTruthy();
    expect(screen.getByText(/Reply by/)).toBeTruthy();
    expect(screen.getByText(/Leave blank for/)).toBeTruthy();
  });

  it("limits the reply-by date to the day before the first day", () => {
    show();
    const input = screen.getByLabelText(/Reply by/) as HTMLInputElement;
    // "Next 7 days" starts the day after tomorrow, so replies close by tomorrow night.
    expect(input.max).toBe(shiftKey(todayKey(), 1));
    expect(screen.getByText(/so every day can still be chosen/)).toBeTruthy();
  });

  it("explains the short reply time when the first day is today", () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Choose dates" }));
    fireEvent.click(screen.getByRole("button", { name: dayLabel(todayKey()) }));
    expect(screen.queryByLabelText(/Reply by/)).toBeNull();
    expect(screen.getByText(/Replies close 3 hours after you post/)).toBeTruthy();
  });

  it("posts a custom length", async () => {
    show();
    fireEvent.change(screen.getByPlaceholderText("Project meeting"), { target: { value: "Dinner" } });
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(screen.getByRole("group", { name: "Custom duration" })).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
    fireEvent.change(screen.getByLabelText("Hours"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Minutes"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Post to group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      title: "Dinner",
      minutes: 150,
      first: shiftKey(todayKey(), 2),
      last: shiftKey(todayKey(), 8),
      reply_by: null,
    });
    expect(await screen.findByText("My times page")).toBeTruthy();
  });

  it("posts the dates picked by hand", async () => {
    show();
    fireEvent.change(screen.getByPlaceholderText("Project meeting"), { target: { value: "Dinner" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose dates" }));
    const from = shiftKey(todayKey(), 2);
    const to = shiftKey(todayKey(), 4);
    // Move to the next month if the selected dates cross a month boundary.
    if (!screen.queryByRole("button", { name: dayLabel(from) })) fireEvent.click(screen.getByRole("button", { name: "Next month" }));
    fireEvent.click(screen.getByRole("button", { name: dayLabel(from) }));
    if (!screen.queryByRole("button", { name: dayLabel(to) })) fireEvent.click(screen.getByRole("button", { name: "Next month" }));
    fireEvent.click(screen.getByRole("button", { name: dayLabel(to) }));
    fireEvent.click(screen.getByRole("button", { name: "Post to group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ first: from, last: to, selected_dates: [from, to] });
  });

  it("rejects an out-of-range custom duration and returns cleanly to a preset", async () => {
    show();
    fireEvent.change(screen.getByLabelText("Purpose"), { target: { value: "Consultation" } });
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Hours"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Minutes"), { target: { value: "1" } });
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Post to group" }));
    expect(sent).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "30 min" }));
    expect(screen.queryByRole("group", { name: "Custom duration" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Post to group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].minutes).toBe(30);
  });
});
