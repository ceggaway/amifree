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
  it("checks the next 7 days and explains the deadline", () => {
    show();
    expect(screen.queryByText("When")).toBeNull();
    expect(screen.getByText(/Deadline to fill in availabilities/)).toBeTruthy();
    expect(screen.getByText(/Then AmIFree suggests the best times/)).toBeTruthy();
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
});
