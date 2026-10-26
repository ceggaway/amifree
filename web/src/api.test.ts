import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./telegram", () => ({ initData: () => "signed" }));

import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("signs in once when several requests start together", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        calls.push(path);
        const body = path === "/api/auth/telegram" ? { token: "t" } : { ok: true };
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    await Promise.all([api.me(), api.plans(), api.group("n1")]);
    expect(calls.filter((path) => path === "/api/auth/telegram")).toHaveLength(1);
    expect(calls).toHaveLength(4);
  });
});
