import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { MultiDatePicker } from "./MultiDatePicker";

function Picker() {
  const [dates, setDates] = useState<string[]>([]);
  return <MultiDatePicker value={dates} onChange={setDates} />;
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-09T12:00:00+08:00"));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("individual poll dates", () => {
  it("selects non-consecutive dates in sorted order and lets users untick them", () => {
    render(<Picker />);
    fireEvent.click(screen.getByRole("button", { name: "15 Nov (Sun)" }));
    fireEvent.click(screen.getByRole("button", { name: "11 Nov (Wed)" }));
    fireEvent.click(screen.getByRole("button", { name: "13 Nov (Fri)" }));
    expect(screen.getByText("Selected: 11 Nov (Wed), 13 Nov (Fri), 15 Nov (Sun)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "12 Nov (Thu)" }).getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "13 Nov (Fri)" }));
    expect(screen.getByText("Selected: 11 Nov (Wed), 15 Nov (Sun)")).toBeTruthy();
  });
  it("blocks past dates and dates beyond the supported window, with month navigation", () => {
    render(<Picker />);
    expect((screen.getByRole("button", { name: "8 Nov (Sun)" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "11 Nov (Wed)" }));
    expect((screen.getByRole("button", { name: "25 Nov (Wed)" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "11 Nov (Wed)" }));
    fireEvent.click(screen.getByRole("button", { name: "Next month" }));
    expect(screen.getByText("December 2026")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "2 Dec (Wed)" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    fireEvent.click(screen.getByRole("button", { name: "30 Nov (Mon)" }));
    expect(screen.getByText("Selected: 30 Nov (Mon), 2 Dec (Wed)")).toBeTruthy();
  });
});
