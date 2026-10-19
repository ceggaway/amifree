-- Apply once after 014; safe to reapply. Google Calendar sharing: a student shares their
-- calendar with AmIFree's own Google account, and AmIFree adds the plans they say Going to.
begin;
alter table public.users add column if not exists google_connected boolean not null default false;

create table if not exists public.google_connections (
  user_id uuid primary key references public.users(id) on delete cascade,
  calendar_email text not null check (length(calendar_email) between 3 and 254),
  -- The busy-times copy of the same calendar; removing it disconnects Google as well.
  calendar_id uuid references public.personal_calendars(id) on delete cascade,
  connected_at timestamptz not null default now(),
  synced_at timestamptz,
  error text
);
create index if not exists google_connections_synced on public.google_connections(synced_at);

-- Which plans are already in the student's Google Calendar, and at which version.
create table if not exists public.google_event_links (
  user_id uuid not null references public.google_connections(user_id) on delete cascade,
  event_id uuid not null references public.events(id) on delete cascade,
  synced_version integer not null,
  primary key (user_id, event_id)
);

alter table public.google_connections enable row level security;
alter table public.google_event_links enable row level security;
revoke all on public.google_connections, public.google_event_links from anon,authenticated;
grant all on public.google_connections, public.google_event_links to service_role;

insert into public.schema_migrations(version,name) values (15,'google_calendar')
on conflict (version) do nothing;
commit;
