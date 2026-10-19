import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, HashRouter, Link, Route, RouterProvider, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BackButton, parentOf } from "./BackButton";

afterEach(() => {
  cleanup();
  delete window.Telegram;
  window.history.replaceState(null, "");
});

function app(start: string) {
  const page = (name: string, next?: string) => (
    <>
      <BackButton />
      <p>{name}</p>
      {next && <Link to={next}>next</Link>}
    </>
  );
  const router = createMemoryRouter(
    [
      { path: "/g/:code", element: page("Group", "/g/n1/new") },
      { path: "/g/:code/new", element: page("New event") },
      { path: "/e/:id", element: page("Event") },
    ],
    { initialEntries: [start] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("Back", () => {
  it("knows the parent of pages opened straight from a link", () => {
    expect(parentOf("/g/n1/new")).toBe("/g/n1");
    expect(parentOf("/g/n1/stats")).toBe("/g/n1");
    expect(parentOf("/poll/abc/mine")).toBe("/poll/abc");
    expect(parentOf("/e/abc")).toBeNull();
  });

  it("shows nothing on the page the app opened on", () => {
    app("/e/abc");
    expect(screen.queryByText("‹ Back")).toBeNull();
  });

  it("goes up from a form opened straight from a group link", async () => {
    app("/g/n1/new");
    fireEvent.click(screen.getByText("‹ Back"));
    expect(await screen.findByText("Group")).toBeTruthy();
  });

  it("uses Telegram's own back arrow inside Telegram", async () => {
    let pressed: (() => void) | undefined;
    const BackButton = { show: vi.fn(), hide: vi.fn(), onClick: vi.fn((cb) => (pressed = cb)), offClick: vi.fn() };
    window.Telegram = { WebApp: { initData: "", ready() {}, expand() {}, isVersionAtLeast: () => true, BackButton } };
    app("/g/n1/new");
    expect(screen.queryByText("‹ Back")).toBeNull(); // no second button in the page
    expect(BackButton.show).toHaveBeenCalled();
    await act(async () => pressed!());
    expect(await screen.findByText("Group")).toBeTruthy();
    expect(BackButton.hide).toHaveBeenCalled(); // the group page is where the app started
  });

  it("returns to the previous page in the real app router", async () => {
    window.location.hash = "#/g/n1";
    render(
      <HashRouter>
        <BackButton />
        <Routes>
          <Route path="/g/:code" element={<Link to="/e/abc">Open event</Link>} />
          <Route path="/e/:id" element={<p>Event page</p>} />
        </Routes>
      </HashRouter>,
    );
    expect(screen.queryByText("‹ Back")).toBeNull(); // the first page has nowhere to go back to
    fireEvent.click(screen.getByText("Open event"));
    expect(await screen.findByText("Event page")).toBeTruthy();
    fireEvent.click(screen.getByText("‹ Back"));
    expect(await screen.findByText("Open event")).toBeTruthy();
  });
});
