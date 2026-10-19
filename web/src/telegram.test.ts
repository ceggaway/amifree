import { afterEach, describe, expect, it, vi } from "vitest";
import { confirm } from "./telegram";

afterEach(() => {
  delete window.Telegram;
  vi.restoreAllMocks();
});

describe("confirm", () => {
  it("uses Telegram's own dialog inside Telegram", async () => {
    const native = vi.spyOn(window, "confirm");
    const showConfirm = vi.fn((_message: string, done: (ok: boolean) => void) => done(true));
    window.Telegram = { WebApp: { initData: "", ready() {}, expand() {}, showConfirm, isVersionAtLeast: () => true } };
    expect(await confirm("Cancel?")).toBe(true);
    expect(showConfirm).toHaveBeenCalledWith("Cancel?", expect.any(Function));
    expect(native).not.toHaveBeenCalled();
  });

  it("falls back to the browser's dialog outside Telegram or on old versions", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    expect(await confirm("Cancel?")).toBe(false);
    const showConfirm = vi.fn();
    window.Telegram = { WebApp: { initData: "", ready() {}, expand() {}, showConfirm, isVersionAtLeast: () => false } };
    expect(await confirm("Cancel?")).toBe(false);
    expect(showConfirm).not.toHaveBeenCalled();
  });
});
