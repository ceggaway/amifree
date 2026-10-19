import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { StartRoute } from "./App";

afterEach(() => {
  cleanup();
  delete window.Telegram;
});

describe("opening the app from a group link", () => {
  it("lands on the group once, then lets you move to other pages", async () => {
    window.Telegram = { WebApp: { initData: "start_param=g_n100500", ready() {}, expand() {} } };
    render(
      <MemoryRouter initialEntries={["/"]}>
        <StartRoute />
        <Routes>
          <Route path="/" element={<p>Home page</p>} />
          <Route path="/g/:code" element={<Link to="/g/n100500/new">Create an event</Link>} />
          <Route path="/g/:code/new" element={<p>New event form</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("Create an event"));
    // Before the fix, the start link sent this straight back to the group page.
    expect(await screen.findByText("New event form")).toBeTruthy();
  });
});
