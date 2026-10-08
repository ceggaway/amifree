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
};
