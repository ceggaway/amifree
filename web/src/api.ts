import { initData } from "./telegram";
import type { Availability, Block, Me, Plan, Rule } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : "Request failed");
  }
}

// Browser-only UI work: no Telegram signature exists outside Telegram, so development
// builds may use canned data. import.meta.env.DEV is false in production builds, which
// removes this branch and the mock module entirely.
export const mockMode = import.meta.env.DEV && !initData();

// Kept in memory only; a fresh sign-in happens each time the Mini App is opened.
let token = "";

async function signIn() {
  const response = await fetch("/api/auth/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData() }),
  });
  if (!response.ok) throw new ApiError(response.status, "Open AmIFree from Telegram to sign in.");
  token = (await response.json()).token;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  if (mockMode) return (await import("./mock")).mock<T>(method, path, body);
  if (!token) await signIn();
  const response = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && retry) {
    token = "";
    return request<T>(method, path, body, false);
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data?.detail);
  return data as T;
}

const range = (start: Date, end: Date) =>
  `start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;

export const api = {
  me: () => request<Me>("GET", "/api/me"),
  plans: () => request<{ plans: Plan[] }>("GET", "/api/plans"),
  calendar: (start: Date, end: Date) =>
    request<{ plans: Plan[]; blocks: Block[] }>("GET", `/api/calendar?${range(start, end)}`),
  availability: (start: Date, end: Date) =>
    request<Availability>("GET", `/api/availability?${range(start, end)}`),
  blocks: () => request<{ blocks: Rule[] }>("GET", "/api/blocks"),
  block: (text: string, confirm: boolean) =>
    request<{ saved: boolean; block: Rule }>("POST", "/api/blocks", { text, confirm }),
  removeBlock: (id: string) => request<void>("DELETE", `/api/blocks/${id}`),
  patchPlan: (id: string, body: Record<string, unknown>) =>
    request<Plan>("PATCH", `/api/plans/${id}`, body),
};
