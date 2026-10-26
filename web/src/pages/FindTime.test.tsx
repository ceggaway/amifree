import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    expect(screen.getByText("Which days?")).toBeTruthy();
    expect(screen.getByText(/Reply by/)).toBeTruthy();
    expect(screen.getByText(/Leave blank for/)).toBeTruthy();
  });

  it("posts a custom length", async () => {
    show();
    fireEvent.change(screen.getByPlaceholderText("Project meeting"), { target: { value: "Dinner" } });
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Length"), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "Post the poll to the group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      title: "Dinner",
      minutes: 150,
      first: shiftKey(todayKey(), 1),
      last: shiftKey(todayKey(), 7),
      reply_by: null,
    });
    expect(await screen.findByText("My times page")).toBeTruthy();
  });

  it("posts the dates picked by hand", async () => {
    show();
    fireEvent.change(screen.getByPlaceholderText("Project meeting"), { target: { value: "Dinner" } });
    fireEvent.click(screen.getByRole("button", { name: "Pick dates" }));
    const from = shiftKey(todayKey(), 2);
    const to = shiftKey(todayKey(), 4);
    fireEvent.change(screen.getByLabelText("From"), { target: { value: from } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: to } });
    fireEvent.click(screen.getByRole("button", { name: "Post the poll to the group" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ first: from, last: to });
  });
});
