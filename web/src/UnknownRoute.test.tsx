import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./pages/Home", () => ({ Home: () => <p>Home page</p> }));

import { App } from "./App";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("an address no page matches", () => {
  it("shows the home page instead of a blank page", async () => {
    // What Telegram's Open button lands on if its launch data is still in the address.
    window.history.replaceState(null, "", "/#tgWebAppVersion=7.10&tgWebAppPlatform=macos");
    render(<App />);
    expect(await screen.findByText("Home page")).toBeTruthy();
  });
});
