// Development-only canned data. Never imported in production builds (see api.ts).
import type { Availability, GroupEventDetail, Plan, PollAnswer, PollDetail, Rule } from "./types";

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
let reminders = true;
let mockPoll: PollDetail = {
  id: "mock",
  chat: "n100500",
  title: "Project meeting",
  status: "OPEN",
  duration_minutes: 120,
  deadline_at: at(1, 23),
  joined: 5,
  joined_by_me: true,
  min_joined: 3,
  slots: [2, 3, 4].map((offset, i) => ({ start_at: at(offset, 14), end_at: at(offset, 16), free: 5 - i, of: 5, yes: 3 - i })),
  mine: {},
  organiser: true,
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
let groupEvent: GroupEventDetail = {
  id: "ge1",
  chat: "n100500",
  title: "CCA dinner",
  start_at: at(2, 19),
  end_at: at(2, 21),
  location: "Vivo",
  notes: "Bring your hall card",
  status: "ACTIVE",
  version: 1,
  ended: false,
  recurring: false,
  counts: { GOING: 2, DECLINED: 0 },
  my_status: null,
  availability: free,
  going: ["Angel", "Rou Shuen"],
  checked_in: false,
  check_in_open: false,
  organiser: true,
  manage: {
    summary: { going: 2, confirmed: 0, unsure: 0, dropped: 0, no_reply: 2 },
    came: 0,
    people: [
      { id: "u1", name: "Angel", answer: "GOING", present: false },
      { id: "u2", name: "Rou Shuen", answer: "GOING", present: false },
      { id: "u3", name: "runezy", answer: "DECLINED", present: false },
    ],
  },
};
let rules: Rule[] = [
  { id: "r1", title: "Work", kind: "WEEKLY", description: "Work\nEvery Wednesday 18:00 → 22:00" },
];

export async function mock<T>(method: string, path: string, body?: unknown): Promise<T> {
  const data = (body ?? {}) as Record<string, unknown>;
  let result: unknown;
  if (path.includes("/stats")) {
    result = { days: 120, held: 6, cancelled: 1, going: 48, came: 39, turnout: "81%", pro_required: false, by_event: [{ title: "Training", sessions: 5, going: 40, came: 33, turnout: "82%" }, { title: "Welcome tea", sessions: 1, going: 8, came: 6, turnout: "75%" }] };
  } else if (path.endsWith("/export")) {
    result = { message: "📬 Sent the attendance file to your chat with AmIFree." };
  } else if (path === "/api/me" && method === "PATCH") {
    reminders = Boolean(data.reminders_enabled);
    result = { reminders_enabled: reminders };
  } else if (path.startsWith("/api/groups/") && method === "GET") {
    result = {
      chat: "n100500",
      title: "CS3216 group",
      events: [{ ...groupEvent, recurring: false }],
      polls: [{ id: "mock", title: "Project meeting", status: "COLLECTING", duration_minutes: 120, joined: 3, joined_by_me: false, organiser: true, availability_submitted: false, deadline_at: at(1, 23), min_joined: 3 }],
      admin: true,
    };
  } else if (path.startsWith("/api/groups/")) {
    result = { id: path.endsWith("/polls") ? "mock" : groupEvent.id };
  } else if (path.startsWith("/api/group-events/")) {
    if (path.endsWith("/rsvp")) {
      groupEvent = { ...groupEvent, my_status: data.status as GroupEventDetail["my_status"] };
    } else if (path.endsWith("/attendance")) {
      const people = groupEvent.manage!.people.map((p) =>
        data.user_id === null || data.user_id === undefined ? { ...p, present: p.answer === "GOING" || p.present } : p.id === data.user_id ? { ...p, present: Boolean(data.present) } : p,
      );
      groupEvent = { ...groupEvent, manage: { ...groupEvent.manage!, people, came: people.filter((p) => p.present).length } };
    } else if (path.endsWith("/nudge") || path.endsWith("/series")) {
      return { message: "Nudging 1 member privately." } as T;
    } else if (method === "PATCH") {
      groupEvent = { ...groupEvent, ...data, version: groupEvent.version + 1 } as GroupEventDetail;
      return { event: groupEvent, series_followup: false } as T;
    } else if (path.endsWith("/cancel")) {
      groupEvent = { ...groupEvent, status: "CANCELLED" };
      return { event: groupEvent, series_followup: false } as T;
    }
    result = groupEvent;
  } else if (path === "/api/me") {
    result = { telegram_user_id: 1, reminders_enabled: reminders, nusmods: { connected: false }, calendars: [], outside_calendars_connected: false };
  } else if (/^\/api\/polls\/[^/]+(\/(join|vote))?$/.test(path)) {
    // One vote each: voting for a time clears the others.
    if (path.endsWith("/vote")) mockPoll = { ...mockPoll, mine: (data.answer as PollAnswer) === "FREE" ? { [String(data.slot)]: "FREE" } : {} };
    result = mockPoll;
  } else if (/^\/api\/polls\/[^/]+\/(suggest|nudge)$/.test(path)) {
    result = { message: "Nudging 2 members privately." };
  } else if (path.endsWith("/pick")) {
    result = { event_id: groupEvent.id };
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
      min_known: 2,
      ready: true,
      max_free: 6,
      status: "OPEN",
      organiser: true,
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
