-- Apply once after 008; safe to reapply. Group features: poll deadlines, "still coming?"
-- checks, recurring events, attendance and export, Clubs Pro plans, optional event detection.
begin;

-- /whosfree: members answer by this time; slots are suggested automatically when it passes.
alter table public.polls add column if not exists deadline_at timestamptz;

-- Recurring events: one series row, one group_events row per session. Sessions are inert
-- (no message_id, so never chased or asked about) until their card is posted.
create table if not exists public.event_series (
  id uuid primary key default gen_random_uuid(),
  chat_id bigint not null,
  source_message_id bigint,
  creator_user_id uuid references public.users(id) on delete set null,
  title text not null check (length(title) between 1 and 200),
  weekdays integer[] not null check (cardinality(weekdays) between 1 and 7),
  start_time time not null, end_time time not null,
  location text, notes text,
  first_date date not null, until_date date not null,
  status text not null default 'ACTIVE' check (status in ('ACTIVE','STOPPED')),
  created_at timestamptz not null default now(),
  unique(chat_id,source_message_id), check (until_date >= first_date)
);

alter table public.group_events alter column source_message_id drop not null;
alter table public.group_events add column if not exists series_id uuid
  references public.event_series(id) on delete set null;
alter table public.group_events add column if not exists occurrence_date date;
-- True once one session was changed on its own; "change all future sessions" skips it.
alter table public.group_events add column if not exists edited_alone boolean not null default false;
-- Set once the card was redrawn in its "ended" state.
alter table public.group_events add column if not exists ended_at timestamptz;
create unique index if not exists group_events_series_day
  on public.group_events(series_id,occurrence_date) where series_id is not null;
create index if not exists group_events_series on public.group_events(series_id) where series_id is not null;

-- Names are kept per group, only for people who used the bot there, so organisers can export
-- attendance. They go with the user (cascade) and with /deletemydata.
alter table public.group_members add column if not exists display_name text;
alter table public.group_members add column if not exists username text;

alter table public.notification_logs drop constraint if exists notification_logs_kind_check;
alter table public.notification_logs add constraint notification_logs_kind_check
  check (kind in ('NIGHTLY','CHANGE','NUDGE','CONFIRM'));

-- "Still coming?" answers. No row means the member has not answered.
create table if not exists public.attendance_checks (
  group_event_id uuid not null references public.group_events(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  answer text not null check (answer in ('CONFIRMED','UNSURE','DROPPED')),
  answered_at timestamptz not null default now(),
  primary key (group_event_id,user_id)
);

-- Who actually came: members check themselves in while the event is on.
create table if not exists public.attendance (
  group_event_id uuid not null references public.group_events(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  present boolean not null default true,
  marked_by uuid references public.users(id) on delete set null,
  marked_at timestamptz not null default now(),
  primary key (group_event_id,user_id)
);

-- Clubs Pro: export and turnout reports. Set by hand until payments exist.
create table if not exists public.group_plans (
  chat_id bigint primary key,
  tier text not null default 'FREE' check (tier in ('FREE','PRO')),
  expires_at timestamptz,
  updated_at timestamptz not null default now()
);

-- Event detection is off until a group admin turns it on.
create table if not exists public.group_settings (
  chat_id bigint primary key,
  detection_enabled boolean not null default false,
  enabled_by_telegram_id bigint,
  enabled_at timestamptz
);

-- A suggestion posted by event detection. Holds the proposed card, never the message text.
create table if not exists public.event_suggestions (
  id uuid primary key default gen_random_uuid(),
  chat_id bigint not null,
  source_message_id bigint not null,
  message_id bigint,
  draft jsonb not null,
  created_at timestamptz not null default now(),
  unique(chat_id,source_message_id)
);
create index if not exists event_suggestions_created on public.event_suggestions(created_at);

-- Same as 003, plus: a change made to one session of a series marks it as edited on its own.
create or replace function public.amifree_update_group_event(
  p_user_id uuid,p_group_event_id uuid,p_draft jsonb,p_version integer,p_cancel boolean
) returns setof public.group_events language plpgsql set search_path = public, pg_temp as $$
declare old public.group_events; changed public.group_events;
begin
  select * into old from public.group_events
    where id=p_group_event_id and creator_user_id=p_user_id and version=p_version and status='ACTIVE'
    for update;
  if not found then return; end if;
  update public.group_events set
    title=case when p_cancel then title else p_draft->>'title' end,
    start_at=case when p_cancel then start_at else (p_draft->>'start_at')::timestamptz end,
    end_at=case when p_cancel then end_at else (p_draft->>'end_at')::timestamptz end,
    location=case when p_cancel then location else p_draft->>'location' end,
    notes=case when p_cancel then notes else p_draft->>'notes' end,
    assumed_duration=case when p_cancel then assumed_duration else coalesce((p_draft->>'assumed_duration')::boolean,false) end,
    status=case when p_cancel then 'CANCELLED' else status end,
    edited_alone=case when not p_cancel and old.series_id is not null then true else edited_alone end,
    -- A moved start needs a fresh "ended" check and a fresh "still coming?" ask.
    ended_at=case when p_cancel then ended_at else null end,
    version=version+1,updated_at=now()
  where id=old.id returning * into changed;
  insert into public.event_changes(group_event_id,version,before,after,changed_by)
    values(changed.id,changed.version,to_jsonb(old),to_jsonb(changed),p_user_id);
  insert into public.notification_logs(user_id,kind,dedupe_key,payload)
  select e.user_id,'CHANGE',changed.id::text||':'||changed.version,
    jsonb_build_object('group_event_id',changed.id,'event_id',e.id,'cancelled',p_cancel,
      'before',to_jsonb(old),'after',to_jsonb(changed))
  from public.events e
  where e.group_event_id=changed.id and e.user_id<>p_user_id and e.status in ('GOING','MAYBE','SAVED')
  on conflict(user_id,kind,dedupe_key) do nothing;
  update public.events set title=changed.title,start_at=changed.start_at,end_at=changed.end_at,
    location=changed.location,notes=changed.notes,assumed_duration=changed.assumed_duration,
    status=case when p_cancel then 'CANCELLED' else status end,
    version=version+1,updated_at=now()
  where group_event_id=changed.id and status<>'CANCELLED';
  return next changed;
end $$;

alter table public.event_series enable row level security;
alter table public.attendance_checks enable row level security;
alter table public.attendance enable row level security;
alter table public.group_plans enable row level security;
alter table public.group_settings enable row level security;
alter table public.event_suggestions enable row level security;
revoke all on public.event_series,public.attendance_checks,public.attendance,
  public.group_plans,public.group_settings,public.event_suggestions from anon,authenticated;
grant all on public.event_series,public.attendance_checks,public.attendance,
  public.group_plans,public.group_settings,public.event_suggestions to service_role;

insert into public.schema_migrations(version,name) values (9,'group_features')
on conflict (version) do nothing;
commit;
