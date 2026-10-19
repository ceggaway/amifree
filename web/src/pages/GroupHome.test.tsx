import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupOverview } from "../types";

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
  it("links to turnout for everyone, and hides detection from non-admins", async () => {
    show(base);
    expect(await screen.findByText("Turnout and attendance")).toBeTruthy();
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
