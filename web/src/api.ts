import { initData } from "./telegram";
import type {
  Answer,
  Availability,
  Block,
  EventForm,
  GroupEventDetail,
  GroupOverview,
  GroupStats,
  Heatmap,
  Me,
  MyGrid,
  Plan,
  PollAnswer,
  PollDetail,
  PollForm,
  Rule,
} from "./types";

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

// One sign-in at a time: the first page often fires several requests together, and each
// would otherwise sign in separately.
let signingIn: Promise<void> | null = null;

function signIn(): Promise<void> {
  signingIn ??= signInOnce().finally(() => (signingIn = null));
  return signingIn;
}

async function signInOnce() {
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
  setReminders: (on: boolean) =>
    request<{ reminders_enabled: boolean }>("PATCH", "/api/me", { reminders_enabled: on }),
  plans: () => request<{ plans: Plan[] }>("GET", "/api/plans"),
  calendar: (start: Date, end: Date) =>
    request<{ plans: Plan[]; blocks: Block[] }>("GET", `/api/calendar?${range(start, end)}`),
  availability: (start: Date, end: Date) =>
    request<Availability>("GET", `/api/availability?${range(start, end)}`),
  blocks: () => request<{ blocks: Rule[] }>("GET", "/api/blocks"),
  block: (text: string, confirm: boolean) =>
    request<{ saved: boolean; block: Rule }>("POST", "/api/blocks", { text, confirm }),
  removeBlock: (id: string) => request<void>("DELETE", `/api/blocks/${id}`),
  poll: (id: string) => request<PollDetail>("GET", `/api/polls/${id}`),
  joinPoll: (id: string) => request<PollDetail>("POST", `/api/polls/${id}/join`),
  vote: (id: string, slot: number, answer: PollAnswer) =>
    request<PollDetail>("POST", `/api/polls/${id}/vote`, { slot, answer }),
  suggest: (id: string) => request<{ message: string }>("POST", `/api/polls/${id}/suggest`),
  pick: (id: string, slot: number) => request<{ event_id: string }>("POST", `/api/polls/${id}/pick`, { slot }),
  pickTime: (id: string, start_at: string) =>
    request<{ event_id: string }>("POST", `/api/polls/${id}/pick-time`, { start_at }),
  cancelPoll: (id: string) => request<{ message: string }>("POST", `/api/polls/${id}/cancel`),
  nudgePoll: (id: string) => request<{ message: string }>("POST", `/api/polls/${id}/nudge`),
  pollHeatmap: (id: string) => request<Heatmap>("GET", `/api/polls/${id}/heatmap`),
  myGrid: (id: string) => request<MyGrid>("GET", `/api/polls/${id}/availability`),
  saveGrid: (id: string, free: string[]) =>
    request<{ saved: number; dropped: number }>("PUT", `/api/polls/${id}/availability`, { free }),
  patchPlan: (id: string, body: Record<string, unknown>) =>
    request<Plan>("PATCH", `/api/plans/${id}`, body),

  // A group, opened from a link in it. The server checks with Telegram that you're in it.
  group: (code: string) => request<GroupOverview>("GET", `/api/groups/${code}`),
  groupStats: (code: string, days: number) => request<GroupStats>("GET", `/api/groups/${code}/stats?days=${days}`),
  exportAttendance: (code: string, days: number) =>
    request<{ message: string }>("POST", `/api/groups/${code}/export`, { days }),
  createEvent: (code: string, form: EventForm) =>
    request<{ id: string }>("POST", `/api/groups/${code}/events`, form),
  createPoll: (code: string, form: PollForm) =>
    request<{ id: string }>("POST", `/api/groups/${code}/polls`, form),
  groupEvent: (id: string) => request<GroupEventDetail>("GET", `/api/group-events/${id}`),
  rsvp: (id: string, status: Answer, force = false) =>
    request<GroupEventDetail>("POST", `/api/group-events/${id}/rsvp`, { status, force }),
  checkIn: (id: string) => request<GroupEventDetail>("POST", `/api/group-events/${id}/check-in`),
  goingToAll: (id: string) =>
    request<{ message: string; event: GroupEventDetail }>("POST", `/api/group-events/${id}/going-to-all`),
  editEvent: (id: string, body: Record<string, unknown>) =>
    request<{ event: GroupEventDetail; series_followup: boolean }>("PATCH", `/api/group-events/${id}`, body),
  cancelEvent: (id: string, version: number) =>
    request<{ event: GroupEventDetail; series_followup: boolean }>("POST", `/api/group-events/${id}/cancel`, { version }),
  series: (id: string, action: "all" | "stop") =>
    request<{ message: string }>("POST", `/api/group-events/${id}/series`, { action }),
  nudge: (id: string) => request<{ message: string }>("POST", `/api/group-events/${id}/nudge`),
  markAttendance: (id: string, userId: string | null, present = true) =>
    request<GroupEventDetail>("PUT", `/api/group-events/${id}/attendance`, { user_id: userId, present }),
};

/** The server's reason for a refused change: { message, reason?, availability? }. */
export function refusal(error: unknown): { message: string; reason?: string; availability?: Availability } {
  if (error instanceof ApiError && error.detail && typeof error.detail === "object") {
    return error.detail as { message: string; reason?: string };
  }
  return { message: error instanceof Error && error.message ? error.message : "That didn't work. Try again." };
}
