import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Plan } from "../types";

const patchPlan = vi.fn();

vi.mock("../api", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public detail: unknown,
    ) {
      super("Request failed");
    }
  }
  return { ApiError, api: { patchPlan: (...args: unknown[]) => patchPlan(...args) } };
});

import { ApiError } from "../api";
import { PlanCard } from "./PlanCard";

const plan: Plan = {
  id: "p1",
  title: "CCA dinner",
  start_at: "2026-10-09T11:00:00Z",
  end_at: "2026-10-09T13:00:00Z",
  location: "Vivo",
  notes: null,
  status: "SAVED",
  version: 3,
  shared: false,
  availability: {
    verdict: "FREE",
    covered: true,
    busy_day: false,
    day_count: 0,
    conflicts: [],
    possible: [],
  },
};

beforeEach(() => patchPlan.mockReset());
afterEach(cleanup);

describe("PlanCard", () => {
  it("sends the plan's version with a status change, then reports the change", async () => {
    patchPlan.mockResolvedValue({});
    const onChange = vi.fn();
    render(<PlanCard plan={plan} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /going/i }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(patchPlan).toHaveBeenCalledWith("p1", { version: 3, status: "GOING" });
  });

  it("asks before overriding a clash, and only then forces it", async () => {
    const confirm = vi.spyOn(window, "confirm");
    patchPlan.mockRejectedValueOnce(new ApiError(409, { reason: "clash" })).mockResolvedValue({});
    confirm.mockReturnValue(false);
    render(<PlanCard plan={plan} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /going/i }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(patchPlan).toHaveBeenCalledTimes(1);

    patchPlan.mockReset();
    patchPlan.mockRejectedValueOnce(new ApiError(409, { reason: "clash" })).mockResolvedValue({});
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getAllByRole("button", { name: /going/i })[0]);
    await waitFor(() => expect(patchPlan).toHaveBeenCalledTimes(2));
    expect(patchPlan).toHaveBeenLastCalledWith("p1", { version: 3, status: "GOING", force: true });
  });

  it("explains a stale version and refreshes", async () => {
    patchPlan.mockImplementationOnce(() => Promise.reject(new ApiError(409, { reason: "stale" })));
    const onChange = vi.fn();
    render(<PlanCard plan={plan} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /going/i }));
    await screen.findByText(/changed elsewhere/);
    expect(onChange).toHaveBeenCalled();
  });
});
