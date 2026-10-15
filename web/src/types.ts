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
