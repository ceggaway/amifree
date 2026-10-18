export type Status = "SAVED" | "GOING" | "MAYBE" | "DECLINED" | "CANCELLED" | "PREVIEW";

export type Block = {
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  source: string;
};

export type Availability = {
  verdict: "FREE" | "CLASH" | "UNKNOWN";
  covered: boolean;
  busy_day: boolean;
  day_count: number;
  conflicts: Block[];
  possible: { id: string; title: string }[];
};

export type Plan = {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  notes: string | null;
  status: Status;
  version: number;
  shared: boolean;
  availability: Availability;
};

export type Rule = { id: string; title: string; kind: "ONCE" | "WEEKLY"; description: string };

export type Me = {
  telegram_user_id: number;
  reminders_enabled: boolean;
  nusmods: { connected: boolean; academic_year: string | null; semester: number | null };
  calendars: { label: string; synced_at: string | null; status: "CONNECTED" | "PENDING" | "ERROR" }[];
  outside_calendars_connected: boolean;
};

export type HeatmapDay = { date: string; cells: { start_at: string; free: number }[] };

export type Heatmap = {
  poll_id: string;
  title: string;
  duration_minutes: number;
  joined: number;
  known: number;
  min_known: number;
  ready: boolean;
  max_free: number;
  days: HeatmapDay[];
};

export type CellState = "busy" | "free" | "off";

export type MyGrid = {
  poll_id: string;
  title: string;
  duration_minutes: number;
  closed: boolean;
  deadline_at: string | null;
  known: boolean;
  submitted: boolean;
  from_usual?: boolean;
  days: { date: string; cells: { start_at: string; state: CellState }[] }[];
};

export type Counts = { GOING: number; MAYBE: number; DECLINED: number };
export type Answer = "GOING" | "MAYBE" | "DECLINED";

export type GroupEventSummary = {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  counts: Counts;
  my_status: Status | null;
  recurring: boolean;
  organiser: boolean;
};

export type GroupPoll = {
  id: string;
  title: string;
  status: "COLLECTING" | "OPEN";
  duration_minutes: number;
  joined: number;
  joined_by_me: boolean;
  deadline_at: string | null;
  min_joined: number;
};

export type GroupOverview = {
  chat: string;
  title: string | null;
  events: GroupEventSummary[];
  polls: GroupPoll[];
};

export type Person = { id: string; name: string; answer: string | null; present: boolean };

export type GroupEventDetail = {
  id: string;
  chat: string;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  notes: string | null;
  status: "ACTIVE" | "CANCELLED";
  version: number;
  ended: boolean;
  recurring: boolean;
  counts: Counts;
  my_status: Status | null;
  availability: Availability;
  going: string[];
  checked_in: boolean;
  check_in_open: boolean;
  organiser: boolean;
  manage?: {
    summary: { going: number; confirmed: number; unsure: number; dropped: number; no_reply: number };
    came: number;
    people: Person[];
  };
};

export type EventForm = {
  request_id: string;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  notes: string | null;
  weekdays: number[];
  until: string | null;
};

export type PollForm = {
  request_id: string;
  title: string;
  minutes: number;
  first: string;
  last: string;
  reply_by: string | null;
};
