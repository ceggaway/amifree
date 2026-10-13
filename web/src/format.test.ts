import { describe, expect, it } from "vitest";
import { dayKey, fromInput, span, toInput } from "./format";

describe("Singapore time handling", () => {
  it("groups by the Singapore day, not the viewer's UTC day", () => {
    // 23:30 UTC on the 8th is 07:30 on the 9th in Singapore.
    expect(dayKey("2026-10-08T23:30:00Z")).toBe("2026-10-09");
  });

  it("shows plan times as 24-hour Singapore clock times", () => {
    expect(span("2026-10-09T11:00:00Z", "2026-10-09T13:00:00Z")).toBe("19:00–21:00");
  });

  it("round-trips a datetime-local value with a +08:00 offset", () => {
    const iso = fromInput("2026-10-09T19:00");
    expect(iso).toBe("2026-10-09T19:00:00+08:00");
    expect(toInput(iso)).toBe("2026-10-09T19:00");
  });
});
