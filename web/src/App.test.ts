import { describe, expect, it } from "vitest";
import { startPath } from "./App";

const id = "6d48d779-233f-4fa7-ace0-16cf4c903964";

describe("links from a group open the right page", () => {
  it("maps each start parameter to its page", () => {
    expect(startPath(`ev_${id}`)).toBe(`/e/${id}`);
    expect(startPath("g_n1001234567890")).toBe("/g/n1001234567890");
    expect(startPath("new_n1001234567890")).toBe("/g/n1001234567890/new");
    expect(startPath("find_p42")).toBe("/g/p42/find");
    expect(startPath(`poll_${id}`)).toBe(`/poll/${id}`);
    expect(startPath(`mine_${id}`)).toBe(`/poll/${id}/mine`);
  });

  it("ignores anything else", () => {
    for (const bad of ["", "g_", "g_-100", "new_x1", "ev_123", "evil_n1", "g_n1/../x"]) {
      expect(startPath(bad)).toBeNull();
    }
  });
});
