// Development-only canned data. Never imported in production builds (see api.ts).
import type { Availability, Plan, Rule } from "./types";

const free: Availability = {
  verdict: "UNKNOWN",
  covered: false,
  busy_day: false,
  day_count: 0,
  conflicts: [],
  possible: [],
};
const at = (days: number, hour: number) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
};
let plans: Plan[] = [
  {
    id: "1",
    title: "CCA dinner",
    start_at: at(0, 19),
    end_at: at(0, 21),
    location: "Vivo",
    notes: null,
    status: "GOING",
    version: 1,
    shared: false,
    availability: free,
  },
  {
    id: "2",
    title: "Hall supper",
    start_at: at(1, 20),
    end_at: at(1, 22),
    location: null,
    notes: null,
    status: "SAVED",
    version: 1,
    shared: true,
    availability: {
      ...free,
      verdict: "CLASH",
      conflicts: [
        { title: "Work", start_at: at(1, 18), end_at: at(1, 22), location: null, source: "MANUAL" },
      ],
    },
  },
];
let rules: Rule[] = [
  { id: "r1", title: "Work", kind: "WEEKLY", description: "Work\nEvery Wednesday 18:00 → 22:00" },
];

export async function mock<T>(method: string, path: string, body?: unknown): Promise<T> {
  const data = (body ?? {}) as Record<string, unknown>;
  let result: unknown;
  if (path === "/api/me") {
    result = { telegram_user_id: 1, reminders_enabled: true, nusmods: { connected: false }, calendars: [], outside_calendars_connected: false };
  } else if (path.startsWith("/api/polls/") && path.endsWith("/availability")) {
    if (method === "PUT") {
      result = { saved: ((data.free as string[]) ?? []).length, dropped: 0 };
    } else {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      d.setHours(0, 0, 0, 0);
      result = {
        poll_id: "mock",
        title: "Project meeting",
        duration_minutes: 120,
        closed: false,
        deadline_at: null,
        known: true,
        submitted: false,
        days: [
          {
            date: d.toISOString().slice(0, 10),
            cells: Array.from({ length: 28 }, (_, i) => {
              const start = new Date(d);
              start.setHours(8 + Math.floor(i / 2), (i % 2) * 30);
              return { start_at: start.toISOString(), state: i >= 4 && i < 8 ? "busy" : "free" };
            }),
          },
        ],
      };
    }
  } else if (path.startsWith("/api/polls/")) {
    const day = (offset: number) => {
      const d = new Date();
      d.setDate(d.getDate() + offset);
      d.setHours(0, 0, 0, 0);
      return d;
    };
    result = {
      poll_id: "mock",
      title: "Project meeting",
      duration_minutes: 120,
      joined: 6,
      known: 6,
      min_known: 3,
      ready: true,
      max_free: 6,
      days: [1, 2, 3, 4].map((offset) => {
        const d = day(offset);
        return {
          date: d.toISOString().slice(0, 10),
          cells: Array.from({ length: 28 }, (_, i) => {
            const start = new Date(d);
            start.setHours(8 + Math.floor(i / 2), (i % 2) * 30);
            return { start_at: start.toISOString(), free: (i * 3 + offset * 2) % 7 };
          }),
        };
      }),
    };
  } else if (path.startsWith("/api/plans/")) {
    const id = path.split("/").pop();
    plans = plans.map((p) =>
      p.id === id
        ? { ...p, ...data, status: data.cancel ? "CANCELLED" : ((data.status as Plan["status"]) ?? p.status), version: p.version + 1 }
        : p,
    );
    result = plans.find((p) => p.id === id);
  } else if (path.startsWith("/api/plans")) {
    result = { plans: plans.filter((p) => p.status !== "CANCELLED") };
  } else if (path.startsWith("/api/calendar")) {
    result = { plans, blocks: plans[1].availability.conflicts };
  } else if (path === "/api/blocks" && method === "POST") {
    const rule = { id: String(Date.now()), title: "Busy", kind: "ONCE" as const, description: String(data.text) };
    if (data.confirm) rules = [...rules, rule];
    result = { saved: Boolean(data.confirm), block: rule };
  } else if (path.startsWith("/api/blocks/")) {
    rules = rules.filter((r) => r.id !== path.split("/").pop());
  } else if (path === "/api/blocks") {
    result = { blocks: rules };
  } else {
    result = free;
  }
  return result as T;
}
