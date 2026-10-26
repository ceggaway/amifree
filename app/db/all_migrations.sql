-- ===== schema.sql =====
-- Run once in a fresh Supabase project's SQL editor; safe to reapply.
begin;
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  telegram_user_id bigint not null unique check (telegram_user_id > 0),
  timezone text not null default 'Asia/Singapore' check (timezone = 'Asia/Singapore'),
  onboarding_status text not null default 'NEW' check (onboarding_status in ('NEW','READY')),
  academic_year text, semester integer check (semester between 1 and 4),
  coverage_start timestamptz, coverage_end timestamptz,
  pending jsonb, pending_expires_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  check (coverage_end > coverage_start),
  check (onboarding_status <> 'READY' or (coverage_start is not null and coverage_end is not null))
);
create table if not exists public.timetable_blocks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null, start_at timestamptz not null, end_at timestamptz not null,
  location text, source text not null default 'NUSMODS',
  check (end_at > start_at)
);
create table if not exists public.events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null check (length(title) between 1 and 200),
  start_at timestamptz not null, end_at timestamptz not null,
  location text, notes text, assumed_duration boolean not null default false,
  status text not null default 'PREVIEW' check (status in ('PREVIEW','GOING','MAYBE','DECLINED','CANCELLED')),
  version integer not null default 1,
  source_message_id bigint not null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(user_id,source_message_id), check (end_at > start_at)
);
create table if not exists public.event_extractions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  raw_text text, extracted_json jsonb not null, validation_state text not null,
  created_at timestamptz not null default now()
);
create table if not exists public.user_actions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  event_id uuid references public.events(id) on delete cascade,
  action_type text not null, metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists timetable_user_time on public.timetable_blocks(user_id,start_at,end_at);
create index if not exists events_user_time on public.events(user_id,start_at,end_at) where status='GOING';
create index if not exists events_user_recent on public.events(user_id,created_at desc);
create index if not exists actions_user_time on public.user_actions(user_id,created_at);

create or replace function public.amifree_ensure_user(p_telegram_id bigint)
returns setof public.users language sql set search_path = public, pg_temp as $$
  insert into public.users(telegram_user_id) values(p_telegram_id)
  on conflict(telegram_user_id) do update set updated_at=now() returning *;
$$;

create or replace function public.amifree_import_timetable(
  p_user_id uuid, p_blocks jsonb, p_year text, p_semester integer,
  p_coverage_start timestamptz, p_coverage_end timestamptz
) returns void language plpgsql set search_path = public, pg_temp as $$
begin
  perform 1 from public.users where id=p_user_id for update;
  if not found then raise exception 'Unknown user'; end if;
  delete from public.timetable_blocks where user_id=p_user_id;
  insert into public.timetable_blocks(user_id,title,start_at,end_at,location,source)
  select p_user_id,x.title,x.start_at,x.end_at,x.location,'NUSMODS'
  from jsonb_to_recordset(p_blocks) as x(title text,start_at timestamptz,end_at timestamptz,location text);
  update public.users set onboarding_status='READY', academic_year=p_year, semester=p_semester,
    coverage_start=p_coverage_start,coverage_end=p_coverage_end,pending=null,pending_expires_at=null,updated_at=now()
  where id=p_user_id;
  insert into public.user_actions(user_id,action_type) values(p_user_id,'onboarding_complete');
end $$;

create or replace function public.amifree_create_preview(p_user_id uuid,p_message_id bigint,p_draft jsonb)
returns setof public.events language plpgsql set search_path = public, pg_temp as $$
begin
  insert into public.events(user_id,source_message_id,title,start_at,end_at,location,notes,assumed_duration)
  values(p_user_id,p_message_id,p_draft->>'title',(p_draft->>'start_at')::timestamptz,
    (p_draft->>'end_at')::timestamptz,p_draft->>'location',p_draft->>'notes',
    coalesce((p_draft->>'assumed_duration')::boolean,false))
  on conflict(user_id,source_message_id) do nothing;
  return query select * from public.events where user_id=p_user_id and source_message_id=p_message_id;
end $$;

create or replace function public.amifree_rsvp(p_user_id uuid,p_event_id uuid,p_status text,p_version integer)
returns setof public.events language plpgsql set search_path = public, pg_temp as $$
declare changed public.events;
begin
  if p_status not in ('GOING','MAYBE','DECLINED') then raise exception 'Invalid RSVP'; end if;
  perform 1 from public.users where id=p_user_id for update;
  update public.events set status=p_status,version=version+1,updated_at=now()
  where id=p_event_id and user_id=p_user_id and version=p_version and status<>'CANCELLED'
  returning * into changed;
  if found then
    insert into public.user_actions(user_id,event_id,action_type) values(p_user_id,p_event_id,lower(p_status));
    return next changed;
  end if;
end $$;

create or replace function public.amifree_update_event(p_user_id uuid,p_event_id uuid,p_draft jsonb,p_version integer,p_cancel boolean)
returns setof public.events language plpgsql set search_path = public, pg_temp as $$
declare changed public.events;
begin
  perform 1 from public.users where id=p_user_id for update;
  update public.events set
    title=case when p_cancel then title else p_draft->>'title' end,
    start_at=case when p_cancel then start_at else (p_draft->>'start_at')::timestamptz end,
    end_at=case when p_cancel then end_at else (p_draft->>'end_at')::timestamptz end,
    location=case when p_cancel then location else p_draft->>'location' end,
    notes=case when p_cancel then notes else p_draft->>'notes' end,
    assumed_duration=case when p_cancel then assumed_duration else coalesce((p_draft->>'assumed_duration')::boolean,false) end,
    status=case when p_cancel then 'CANCELLED' else status end,
    version=version+1,updated_at=now()
  where id=p_event_id and user_id=p_user_id and version=p_version and status<>'CANCELLED'
  returning * into changed;
  if found then
    insert into public.user_actions(user_id,event_id,action_type)
      values(p_user_id,p_event_id,case when p_cancel then 'cancelled' else 'edited' end);
    return next changed;
  end if;
end $$;

create or replace function public.amifree_cleanup(p_retention_days integer default 7)
returns void language plpgsql set search_path = public, pg_temp as $$
begin
  if p_retention_days < 1 or p_retention_days > 90 then raise exception 'Invalid retention'; end if;
  delete from public.event_extractions where created_at < now()-make_interval(days=>p_retention_days);
  update public.users set pending=null,pending_expires_at=null where pending_expires_at < now();
end $$;

-- No browser access. Telegram ownership is enforced by the server for every operation.
alter table public.users enable row level security;
alter table public.timetable_blocks enable row level security;
alter table public.events enable row level security;
alter table public.event_extractions enable row level security;
alter table public.user_actions enable row level security;
revoke all on public.users,public.timetable_blocks,public.events,public.event_extractions,public.user_actions from anon,authenticated;
grant all on public.users,public.timetable_blocks,public.events,public.event_extractions,public.user_actions to service_role;
revoke all on function public.amifree_ensure_user(bigint),
  public.amifree_import_timetable(uuid,jsonb,text,integer,timestamptz,timestamptz),
  public.amifree_create_preview(uuid,bigint,jsonb),public.amifree_rsvp(uuid,uuid,text,integer),
  public.amifree_update_event(uuid,uuid,jsonb,integer,boolean),public.amifree_cleanup(integer)
  from public,anon,authenticated;
grant execute on function public.amifree_ensure_user(bigint),
  public.amifree_import_timetable(uuid,jsonb,text,integer,timestamptz,timestamptz),
  public.amifree_create_preview(uuid,bigint,jsonb),public.amifree_rsvp(uuid,uuid,text,integer),
  public.amifree_update_event(uuid,uuid,jsonb,integer,boolean),public.amifree_cleanup(integer) to service_role;
commit;

-- ===== 001_saves_and_manual_blocks.sql =====
-- Apply once after app/db/schema.sql; safe to reapply. Existing rows are preserved.
begin;
-- The status check was created inline, so find it by definition rather than assume its name.
do $$
declare c text;
begin
  for c in select conname from pg_constraint
    where conrelid='public.events'::regclass and contype='c'
      and pg_get_constraintdef(oid) like '%PREVIEW%'
  loop
    execute format('alter table public.events drop constraint %I', c);
  end loop;
end $$;
alter table public.events add constraint events_status_check
  check (status in ('PREVIEW','SAVED','GOING','MAYBE','DECLINED','CANCELLED'));
create index if not exists events_user_time_all on public.events(user_id,start_at,end_at);

-- Kept apart from timetable_blocks: a NUSMods reimport deletes every row of that table.
create table if not exists public.manual_busy_blocks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null check (length(title) between 1 and 200),
  kind text not null check (kind in ('ONCE','WEEKLY')),
  start_at timestamptz, end_at timestamptz,
  weekday smallint check (weekday between 0 and 6),
  start_time time, end_time time, start_date date, until_date date,
  timezone text not null default 'Asia/Singapore' check (timezone = 'Asia/Singapore'),
  created_at timestamptz not null default now(),
  check (
    (kind='ONCE' and start_at is not null and end_at is not null and end_at > start_at)
    or (kind='WEEKLY' and weekday is not null and start_time is not null and end_time is not null
        and end_time > start_time and start_date is not null
        and (until_date is null or until_date >= start_date))
  )
);
create index if not exists manual_blocks_user on public.manual_busy_blocks(user_id);

create or replace function public.amifree_save(p_user_id uuid,p_event_id uuid,p_version integer)
returns setof public.events language plpgsql set search_path = public, pg_temp as $$
declare changed public.events;
begin
  perform 1 from public.users where id=p_user_id for update;
  update public.events set status='SAVED',version=version+1,updated_at=now()
  where id=p_event_id and user_id=p_user_id and version=p_version and status<>'CANCELLED'
  returning * into changed;
  if found then
    insert into public.user_actions(user_id,event_id,action_type) values(p_user_id,p_event_id,'saved');
    return next changed;
  end if;
end $$;

alter table public.manual_busy_blocks enable row level security;
revoke all on public.manual_busy_blocks from anon,authenticated;
grant all on public.manual_busy_blocks to service_role;
revoke all on function public.amifree_save(uuid,uuid,integer) from public,anon,authenticated;
grant execute on function public.amifree_save(uuid,uuid,integer) to service_role;
commit;

-- ===== 002_group_events.sql =====
-- Apply once after 001; safe to reapply. Shared group cards and per-member attendance.
begin;
create table if not exists public.group_events (
  id uuid primary key default gen_random_uuid(),
  chat_id bigint not null,
  -- The /event command message; makes card creation idempotent across webhook retries.
  source_message_id bigint not null,
  -- The bot's own card message, set after Telegram confirms the post.
  message_id bigint,
  creator_user_id uuid references public.users(id) on delete set null,
  title text not null check (length(title) between 1 and 200),
  start_at timestamptz not null, end_at timestamptz not null,
  location text, notes text, assumed_duration boolean not null default false,
  status text not null default 'ACTIVE' check (status in ('ACTIVE','CANCELLED')),
  version integer not null default 1,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(chat_id,source_message_id), check (end_at > start_at)
);
-- People who have used AmIFree in a group. Bots cannot list group members.
create table if not exists public.group_members (
  chat_id bigint not null,
  user_id uuid not null references public.users(id) on delete cascade,
  first_seen timestamptz not null default now(),
  primary key (chat_id,user_id)
);
create table if not exists public.event_changes (
  id uuid primary key default gen_random_uuid(),
  group_event_id uuid not null references public.group_events(id) on delete cascade,
  version integer not null,
  before jsonb not null, after jsonb not null,
  changed_by uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique(group_event_id,version)
);
-- A member's save/RSVP on a group card is their own events row linked to the card.
alter table public.events add column if not exists group_event_id uuid
  references public.group_events(id) on delete set null;
alter table public.events alter column source_message_id drop not null;
create unique index if not exists events_user_group_event
  on public.events(user_id,group_event_id) where group_event_id is not null;
create index if not exists events_group_event on public.events(group_event_id) where group_event_id is not null;
create index if not exists group_events_chat on public.group_events(chat_id,created_at desc);

create or replace function public.amifree_create_group_event(
  p_user_id uuid,p_chat_id bigint,p_source_message_id bigint,p_draft jsonb
) returns setof public.group_events language plpgsql set search_path = public, pg_temp as $$
begin
  perform 1 from public.users where id=p_user_id for update;
  if not found then raise exception 'Unknown user'; end if;
  insert into public.group_members(chat_id,user_id) values(p_chat_id,p_user_id) on conflict do nothing;
  insert into public.group_events(chat_id,source_message_id,creator_user_id,title,start_at,end_at,location,notes,assumed_duration)
  values(p_chat_id,p_source_message_id,p_user_id,p_draft->>'title',(p_draft->>'start_at')::timestamptz,
    (p_draft->>'end_at')::timestamptz,p_draft->>'location',p_draft->>'notes',
    coalesce((p_draft->>'assumed_duration')::boolean,false))
  on conflict(chat_id,source_message_id) do nothing;
  return query select * from public.group_events
    where chat_id=p_chat_id and source_message_id=p_source_message_id;
end $$;

create or replace function public.amifree_touch_member(p_chat_id bigint,p_user_id uuid)
returns void language sql set search_path = public, pg_temp as $$
  insert into public.group_members(chat_id,user_id) values(p_chat_id,p_user_id) on conflict do nothing;
$$;

create or replace function public.amifree_group_rsvp(p_user_id uuid,p_group_event_id uuid,p_status text)
returns setof public.events language plpgsql set search_path = public, pg_temp as $$
declare g public.group_events; changed public.events;
begin
  if p_status not in ('SAVED','GOING','MAYBE','DECLINED') then raise exception 'Invalid RSVP'; end if;
  perform 1 from public.users where id=p_user_id for update;
  if not found then raise exception 'Unknown user'; end if;
  select * into g from public.group_events where id=p_group_event_id and status='ACTIVE';
  if not found then return; end if;
  insert into public.group_members(chat_id,user_id) values(g.chat_id,p_user_id) on conflict do nothing;
  insert into public.events(user_id,group_event_id,title,start_at,end_at,location,notes,assumed_duration,status)
  values(p_user_id,g.id,g.title,g.start_at,g.end_at,g.location,g.notes,g.assumed_duration,p_status)
  on conflict(user_id,group_event_id) where group_event_id is not null do update set
    -- Saving never downgrades an existing answer.
    status=case when excluded.status='SAVED' and events.status in ('GOING','MAYBE')
      then events.status else excluded.status end,
    version=events.version+1,updated_at=now()
  returning * into changed;
  insert into public.user_actions(user_id,event_id,action_type)
    values(p_user_id,changed.id,'group_'||lower(p_status));
  return next changed;
end $$;

-- Headcount only: names of attendees are never returned.
create or replace function public.amifree_group_counts(p_group_event_id uuid)
returns table(status text,n integer) language sql stable set search_path = public, pg_temp as $$
  select e.status,count(*)::integer from public.events e
  where e.group_event_id=p_group_event_id group by e.status;
$$;

create or replace function public.amifree_update_group_event(
  p_user_id uuid,p_group_event_id uuid,p_draft jsonb,p_version integer,p_cancel boolean
) returns setof public.group_events language plpgsql set search_path = public, pg_temp as $$
declare old public.group_events; changed public.group_events;
begin
  -- Only the organiser may change a shared card, and only from its latest version.
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
    version=version+1,updated_at=now()
  where id=old.id returning * into changed;
  insert into public.event_changes(group_event_id,version,before,after,changed_by)
    values(changed.id,changed.version,to_jsonb(old),to_jsonb(changed),p_user_id);
  update public.events set title=changed.title,start_at=changed.start_at,end_at=changed.end_at,
    location=changed.location,notes=changed.notes,assumed_duration=changed.assumed_duration,
    status=case when p_cancel then 'CANCELLED' else status end,
    version=version+1,updated_at=now()
  where group_event_id=changed.id and status<>'CANCELLED';
  return next changed;
end $$;

alter table public.group_events enable row level security;
alter table public.group_members enable row level security;
alter table public.event_changes enable row level security;
revoke all on public.group_events,public.group_members,public.event_changes from anon,authenticated;
grant all on public.group_events,public.group_members,public.event_changes to service_role;
revoke all on function public.amifree_create_group_event(uuid,bigint,bigint,jsonb),
  public.amifree_touch_member(bigint,uuid),public.amifree_group_rsvp(uuid,uuid,text),
  public.amifree_group_counts(uuid),
  public.amifree_update_group_event(uuid,uuid,jsonb,integer,boolean) from public,anon,authenticated;
grant execute on function public.amifree_create_group_event(uuid,bigint,bigint,jsonb),
  public.amifree_touch_member(bigint,uuid),public.amifree_group_rsvp(uuid,uuid,text),
  public.amifree_group_counts(uuid),
  public.amifree_update_group_event(uuid,uuid,jsonb,integer,boolean) to service_role;
commit;

-- ===== 003_notifications.sql =====
-- Apply once after 002; safe to reapply. Durable outbox for reminders, alerts and nudges.
begin;
alter table public.users add column if not exists reminders_enabled boolean not null default true;
-- Set when Telegram refuses a DM (the user blocked the bot or never opened a private chat).
alter table public.users add column if not exists dm_blocked boolean not null default false;

create table if not exists public.notification_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  kind text not null check (kind in ('NIGHTLY','CHANGE','NUDGE')),
  -- With user and kind, identifies the one message that may ever be sent for this cause.
  dedupe_key text not null,
  payload jsonb not null default '{}',
  status text not null default 'pending' check (status in ('pending','sending','sent','failed')),
  attempts integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  claimed_at timestamptz, sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique(user_id,kind,dedupe_key)
);
create index if not exists notifications_due on public.notification_logs(next_attempt_at)
  where status in ('pending','sending');

create or replace function public.amifree_enqueue_notification(
  p_user_id uuid,p_kind text,p_key text,p_payload jsonb
) returns boolean language plpgsql set search_path = public, pg_temp as $$
begin
  insert into public.notification_logs(user_id,kind,dedupe_key,payload)
    values(p_user_id,p_kind,p_key,coalesce(p_payload,'{}'))
    on conflict(user_id,kind,dedupe_key) do nothing;
  return found;
end $$;

-- Claim due rows for one worker. A row left in 'sending' by a crashed worker is
-- reclaimed after ten minutes; that is the only case that can repeat a message.
create or replace function public.amifree_claim_notifications(p_limit integer default 50)
returns setof public.notification_logs language plpgsql set search_path = public, pg_temp as $$
begin
  return query
  update public.notification_logs n set status='sending',attempts=n.attempts+1,claimed_at=now()
  where n.id in (
    select c.id from public.notification_logs c
    where (c.status='pending' and c.next_attempt_at<=now())
       or (c.status='sending' and c.claimed_at<now()-interval '10 minutes')
    order by c.created_at limit greatest(1,least(p_limit,200)) for update skip locked
  )
  returning n.*;
end $$;

-- Mark sent only after Telegram confirmed. Failures back off, then give up after 5 tries.
create or replace function public.amifree_finish_notification(p_id uuid,p_ok boolean,p_permanent boolean)
returns void language sql set search_path = public, pg_temp as $$
  update public.notification_logs set
    status=case when p_ok then 'sent' when p_permanent or attempts>=5 then 'failed' else 'pending' end,
    sent_at=case when p_ok then now() else sent_at end,
    next_attempt_at=now()+make_interval(mins=>power(2,least(attempts,10))::integer)
  where id=p_id and status='sending';
$$;

create or replace function public.amifree_update_group_event(
  p_user_id uuid,p_group_event_id uuid,p_draft jsonb,p_version integer,p_cancel boolean
) returns setof public.group_events language plpgsql set search_path = public, pg_temp as $$
declare old public.group_events; changed public.group_events;
begin
  -- Only the organiser may change a shared card, and only from its latest version.
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
    version=version+1,updated_at=now()
  where id=old.id returning * into changed;
  insert into public.event_changes(group_event_id,version,before,after,changed_by)
    values(changed.id,changed.version,to_jsonb(old),to_jsonb(changed),p_user_id);
  -- One alert per member who kept the plan, queued in the same transaction as the change.
  -- Runs before linked rows are cancelled so their answers are still visible here.
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

alter table public.notification_logs enable row level security;
revoke all on public.notification_logs from anon,authenticated;
grant all on public.notification_logs to service_role;
revoke all on function public.amifree_enqueue_notification(uuid,text,text,jsonb),
  public.amifree_claim_notifications(integer),
  public.amifree_finish_notification(uuid,boolean,boolean) from public,anon,authenticated;
grant execute on function public.amifree_enqueue_notification(uuid,text,text,jsonb),
  public.amifree_claim_notifications(integer),
  public.amifree_finish_notification(uuid,boolean,boolean) to service_role;
commit;

-- ===== 004_polls.sql =====
-- Apply once after 003; safe to reapply. /whosfree polls with opt-in schedule use.
begin;
create table if not exists public.polls (
  id uuid primary key default gen_random_uuid(),
  chat_id bigint not null,
  source_message_id bigint not null,
  message_id bigint,
  creator_user_id uuid references public.users(id) on delete set null,
  title text not null check (length(title) between 1 and 200),
  duration_minutes integer not null check (duration_minutes between 15 and 720),
  window_start timestamptz not null, window_end timestamptz not null,
  status text not null default 'COLLECTING' check (status in ('COLLECTING','OPEN','CLOSED')),
  -- Suggested slots: [{start_at,end_at,free}]. Counts only, never who is busy.
  slots jsonb not null default '[]',
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(chat_id,source_message_id), check (window_end > window_start)
);
-- Members who tapped "Count me in": the only people whose schedules a poll may read.
create table if not exists public.poll_participants (
  poll_id uuid not null references public.polls(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (poll_id,user_id)
);
create table if not exists public.poll_votes (
  poll_id uuid not null references public.polls(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  slot integer not null check (slot between 0 and 2),
  answer text not null check (answer in ('FREE','MAYBE','BUSY')),
  primary key (poll_id,user_id,slot)
);

create or replace function public.amifree_create_poll(
  p_user_id uuid,p_chat_id bigint,p_source_message_id bigint,p_title text,
  p_duration integer,p_window_start timestamptz,p_window_end timestamptz
) returns setof public.polls language plpgsql set search_path = public, pg_temp as $$
declare created public.polls;
begin
  perform 1 from public.users where id=p_user_id for update;
  if not found then raise exception 'Unknown user'; end if;
  insert into public.group_members(chat_id,user_id) values(p_chat_id,p_user_id) on conflict do nothing;
  insert into public.polls(chat_id,source_message_id,creator_user_id,title,duration_minutes,window_start,window_end)
  values(p_chat_id,p_source_message_id,p_user_id,p_title,p_duration,p_window_start,p_window_end)
  on conflict(chat_id,source_message_id) do nothing returning * into created;
  if found then
    -- Asking for the poll is the organiser's own opt-in.
    insert into public.poll_participants(poll_id,user_id) values(created.id,p_user_id);
  end if;
  return query select * from public.polls where chat_id=p_chat_id and source_message_id=p_source_message_id;
end $$;

create or replace function public.amifree_poll_join(p_poll_id uuid,p_user_id uuid)
returns integer language plpgsql set search_path = public, pg_temp as $$
declare p public.polls; total integer;
begin
  select * into p from public.polls where id=p_poll_id and status<>'CLOSED';
  if not found then return null; end if;
  insert into public.group_members(chat_id,user_id) values(p.chat_id,p_user_id) on conflict do nothing;
  insert into public.poll_participants(poll_id,user_id) values(p_poll_id,p_user_id) on conflict do nothing;
  select count(*) into total from public.poll_participants where poll_id=p_poll_id;
  return total;
end $$;

create or replace function public.amifree_poll_open(p_poll_id uuid,p_user_id uuid,p_slots jsonb)
returns setof public.polls language sql set search_path = public, pg_temp as $$
  update public.polls set slots=p_slots,status='OPEN',updated_at=now()
  where id=p_poll_id and creator_user_id=p_user_id and status='COLLECTING'
    and jsonb_array_length(p_slots) between 1 and 3
  returning *;
$$;

create or replace function public.amifree_poll_close(p_poll_id uuid,p_user_id uuid)
returns setof public.polls language sql set search_path = public, pg_temp as $$
  update public.polls set status='CLOSED',updated_at=now()
  where id=p_poll_id and creator_user_id=p_user_id and status='OPEN' returning *;
$$;

-- p_votes maps slot index to answer, e.g. {"0":"FREE","2":"BUSY"}.
create or replace function public.amifree_poll_vote(p_poll_id uuid,p_user_id uuid,p_votes jsonb)
returns boolean language plpgsql set search_path = public, pg_temp as $$
declare p public.polls;
begin
  select * into p from public.polls where id=p_poll_id and status='OPEN';
  if not found then return false; end if;
  insert into public.group_members(chat_id,user_id) values(p.chat_id,p_user_id) on conflict do nothing;
  insert into public.poll_votes(poll_id,user_id,slot,answer)
  select p_poll_id,p_user_id,v.key::integer,v.value from jsonb_each_text(p_votes) v
  where v.key::integer < jsonb_array_length(p.slots)
  on conflict(poll_id,user_id,slot) do update set answer=excluded.answer;
  return true;
end $$;

create or replace function public.amifree_poll_tally(p_poll_id uuid)
returns table(slot integer,answer text,n integer) language sql stable set search_path = public, pg_temp as $$
  select v.slot,v.answer,count(*)::integer from public.poll_votes v
  where v.poll_id=p_poll_id group by v.slot,v.answer;
$$;

alter table public.polls enable row level security;
alter table public.poll_participants enable row level security;
alter table public.poll_votes enable row level security;
revoke all on public.polls,public.poll_participants,public.poll_votes from anon,authenticated;
grant all on public.polls,public.poll_participants,public.poll_votes to service_role;
revoke all on function public.amifree_create_poll(uuid,bigint,bigint,text,integer,timestamptz,timestamptz),
  public.amifree_poll_join(uuid,uuid),public.amifree_poll_open(uuid,uuid,jsonb),
  public.amifree_poll_close(uuid,uuid),public.amifree_poll_vote(uuid,uuid,jsonb),
  public.amifree_poll_tally(uuid) from public,anon,authenticated;
grant execute on function public.amifree_create_poll(uuid,bigint,bigint,text,integer,timestamptz,timestamptz),
  public.amifree_poll_join(uuid,uuid),public.amifree_poll_open(uuid,uuid,jsonb),
  public.amifree_poll_close(uuid,uuid),public.amifree_poll_vote(uuid,uuid,jsonb),
  public.amifree_poll_tally(uuid) to service_role;
commit;

-- ===== 005_calendar_feed.sql =====
-- Apply once after 004; safe to reapply. Private calendar feed: the user subscribes once
-- (Apple Calendar, later Google) and their Going/Saved plans appear and update by themselves.
begin;
alter table public.users add column if not exists calendar_feed_token text;
-- When their calendar app last fetched the feed: proof the subscription is live.
alter table public.users add column if not exists calendar_feed_seen_at timestamptz;
create unique index if not exists users_calendar_feed_token
  on public.users(calendar_feed_token) where calendar_feed_token is not null;
alter table public.users drop constraint if exists users_calendar_feed_token_length;
alter table public.users add constraint users_calendar_feed_token_length
  check (calendar_feed_token is null or length(calendar_feed_token) >= 32);
commit;

-- ===== 006_personal_calendar.sql =====
-- Apply once after 005; safe to reapply. Busy times read from a personal calendar link.
-- Only start/end are stored: there is deliberately no column for titles or details.
begin;
alter table public.users add column if not exists personal_calendar_url text;  -- encrypted
alter table public.users add column if not exists personal_calendar_synced_at timestamptz;
alter table public.users add column if not exists personal_calendar_error text;
-- Last attempt, successful or not: a failing calendar is retried hourly, not every minute.
alter table public.users add column if not exists personal_calendar_checked_at timestamptz;

create table if not exists public.calendar_busy_blocks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  start_at timestamptz not null, end_at timestamptz not null,
  check (end_at > start_at)
);
create index if not exists calendar_blocks_user_time
  on public.calendar_busy_blocks(user_id,start_at,end_at);
alter table public.calendar_busy_blocks enable row level security;
revoke all on public.calendar_busy_blocks from anon,authenticated;
grant all on public.calendar_busy_blocks to service_role;

-- A refresh replaces the user's calendar blocks in one transaction: never half-updated.
create or replace function public.amifree_replace_calendar_blocks(
  p_user_id uuid, p_blocks jsonb, p_synced_at timestamptz
) returns void language plpgsql set search_path = public, pg_temp as $$
begin
  perform 1 from public.users where id=p_user_id for update;
  delete from public.calendar_busy_blocks where user_id=p_user_id;
  insert into public.calendar_busy_blocks(user_id,start_at,end_at)
  select p_user_id, (b->>'start_at')::timestamptz, (b->>'end_at')::timestamptz
  from jsonb_array_elements(p_blocks) b;
  update public.users
  set personal_calendar_synced_at=p_synced_at, personal_calendar_checked_at=p_synced_at,
      personal_calendar_error=null, updated_at=now()
  where id=p_user_id;
end $$;
revoke all on function public.amifree_replace_calendar_blocks(uuid,jsonb,timestamptz)
  from public,anon,authenticated;
grant execute on function public.amifree_replace_calendar_blocks(uuid,jsonb,timestamptz)
  to service_role;
commit;

-- ===== 007_multiple_calendars.sql =====
-- Apply once after 006; safe to reapply. Several personal calendar links per user
-- (e.g. separate iCloud calendars for classes, CCA and events). Existing links move across.
begin;
create table if not exists public.personal_calendars (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  sealed_url text not null,                      -- encrypted link
  url_hash text not null,                        -- keyed hash: spots a re-sent link, reveals nothing
  label text not null check (length(label) between 1 and 60),  -- the calendar's own name
  synced_at timestamptz, checked_at timestamptz, error text,
  created_at timestamptz not null default now(),
  unique (user_id, url_hash)
);
create index if not exists personal_calendars_user on public.personal_calendars(user_id);
alter table public.personal_calendars enable row level security;
revoke all on public.personal_calendars from anon,authenticated;
grant all on public.personal_calendars to service_role;

alter table public.calendar_busy_blocks
  add column if not exists calendar_id uuid references public.personal_calendars(id) on delete cascade;
create index if not exists calendar_blocks_calendar on public.calendar_busy_blocks(calendar_id);

-- Move each existing single link (migration 006) into the new table, with its busy times.
insert into public.personal_calendars(user_id,sealed_url,url_hash,label,synced_at,checked_at,error)
select id, personal_calendar_url, 'legacy-' || id, 'Personal calendar',
       personal_calendar_synced_at, personal_calendar_checked_at, personal_calendar_error
from public.users where personal_calendar_url is not null
on conflict (user_id,url_hash) do nothing;
update public.calendar_busy_blocks b set calendar_id = c.id
from public.personal_calendars c
where b.calendar_id is null and c.user_id = b.user_id and c.url_hash = 'legacy-' || b.user_id;
delete from public.calendar_busy_blocks where calendar_id is null;
update public.users set personal_calendar_url = null, personal_calendar_synced_at = null,
  personal_calendar_checked_at = null, personal_calendar_error = null
where personal_calendar_url is not null;

-- A refresh replaces ONE calendar's blocks in one transaction; the others are untouched.
create or replace function public.amifree_replace_calendar(
  p_calendar_id uuid, p_blocks jsonb, p_synced_at timestamptz
) returns void language plpgsql set search_path = public, pg_temp as $$
declare owner uuid;
begin
  select user_id into owner from public.personal_calendars where id=p_calendar_id for update;
  if owner is null then return; end if;
  delete from public.calendar_busy_blocks where calendar_id=p_calendar_id;
  insert into public.calendar_busy_blocks(user_id,calendar_id,start_at,end_at)
  select owner, p_calendar_id, (b->>'start_at')::timestamptz, (b->>'end_at')::timestamptz
  from jsonb_array_elements(p_blocks) b;
  update public.personal_calendars
  set synced_at=p_synced_at, checked_at=p_synced_at, error=null
  where id=p_calendar_id;
end $$;
revoke all on function public.amifree_replace_calendar(uuid,jsonb,timestamptz)
  from public,anon,authenticated;
grant execute on function public.amifree_replace_calendar(uuid,jsonb,timestamptz) to service_role;
commit;

-- ===== 008_schema_ledger_and_updates.sql =====
-- Apply once after 007; safe to reapply. Adds a migration ledger (the app checks it at
-- startup) and durable Telegram update de-duplication, so a webhook retry after a restart
-- cannot repeat a save, RSVP or delete.
begin;

create table if not exists public.schema_migrations (
  version integer primary key,
  name text not null,
  applied_at timestamptz not null default now()
);
-- Migrations 001-007 predate the ledger and are verified by object checks. From here on every
-- migration records itself, and the app refuses to start below the version it was built for.
insert into public.schema_migrations(version, name)
values (8, 'schema_ledger_and_processed_updates')
on conflict (version) do nothing;

create table if not exists public.processed_updates (
  update_id bigint primary key,
  status text not null default 'processing' check (status in ('processing', 'done')),
  claimed_at timestamptz not null default now()
);
create index if not exists processed_updates_claimed on public.processed_updates(claimed_at);

-- True when this caller should process the update: it is new, or an earlier attempt was
-- claimed but never finished and is older than p_stale_seconds (it probably crashed).
create or replace function public.amifree_claim_update(
  p_update_id bigint, p_stale_seconds integer default 120
) returns boolean language plpgsql set search_path = public, pg_temp as $$
declare claimed boolean;
begin
  delete from public.processed_updates where claimed_at < now() - interval '7 days';
  insert into public.processed_updates(update_id) values (p_update_id)
  on conflict (update_id) do update set claimed_at = now()
    where public.processed_updates.status = 'processing'
      and public.processed_updates.claimed_at < now() - make_interval(secs => p_stale_seconds)
  returning true into claimed;
  return coalesce(claimed, false);
end $$;

create or replace function public.amifree_finish_update(p_update_id bigint)
returns void language sql set search_path = public, pg_temp as $$
  update public.processed_updates set status = 'done' where update_id = p_update_id;
$$;

-- Lets a retry reprocess an update that failed before anything was committed.
create or replace function public.amifree_release_update(p_update_id bigint)
returns void language sql set search_path = public, pg_temp as $$
  delete from public.processed_updates where update_id = p_update_id and status = 'processing';
$$;

alter table public.schema_migrations enable row level security;
alter table public.processed_updates enable row level security;
revoke all on public.schema_migrations, public.processed_updates from anon, authenticated;
grant all on public.schema_migrations, public.processed_updates to service_role;
revoke all on function public.amifree_claim_update(bigint, integer),
  public.amifree_finish_update(bigint), public.amifree_release_update(bigint)
  from public, anon, authenticated;
grant execute on function public.amifree_claim_update(bigint, integer),
  public.amifree_finish_update(bigint), public.amifree_release_update(bigint) to service_role;
commit;

-- ===== 009_group_features.sql =====
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
  -- DEADLINE comes from 012_deadlines.sql; keep it so either order leaves both valid.
  check (kind in ('NIGHTLY','CHANGE','NUDGE','CONFIRM','DEADLINE'));

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

-- ===== 010_availability_and_payments.sql =====
-- Apply once after 009; safe to reapply. Members' own availability for a poll, and Telegram
-- Stars payments for Clubs Pro.
begin;

-- A member's own answer for a /whosfree poll: the half-hour cells (ISO start times) they are
-- free in. Only cells their schedule leaves open can be ticked; the grid never says why a
-- cell is blocked.
create table if not exists public.poll_availability (
  poll_id uuid not null references public.polls(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  free jsonb not null default '[]',
  updated_at timestamptz not null default now(),
  primary key (poll_id,user_id),
  check (jsonb_typeof(free)='array' and jsonb_array_length(free) <= 800)
);

-- One row per Telegram Stars charge, so a replayed payment update never extends Pro twice.
-- Kept after the user is deleted: it is a financial record, and holds no schedule data.
create table if not exists public.pro_payments (
  charge_id text primary key,
  chat_id bigint not null,
  telegram_id bigint not null,
  stars integer not null check (stars >= 0),
  days integer not null check (days > 0),
  paid_at timestamptz not null default now()
);
create index if not exists pro_payments_chat on public.pro_payments(chat_id,paid_at desc);

alter table public.poll_availability enable row level security;
alter table public.pro_payments enable row level security;
revoke all on public.poll_availability,public.pro_payments from anon,authenticated;
grant all on public.poll_availability,public.pro_payments to service_role;

insert into public.schema_migrations(version,name) values (10,'availability_and_payments')
on conflict (version) do nothing;
commit;

-- ===== 011_usual_times_and_refunds.sql =====
-- Apply once after 010; safe to reapply. A member's usual weekly free times, and refunds of
-- Clubs Pro Stars payments.
begin;

-- "Every Tuesday evening": windows in minutes since midnight, Singapore time, weekday 0 = Monday.
-- Used to pre-fill a poll grid, and to count the member as known when they have no schedule.
create table if not exists public.weekly_availability (
  user_id uuid not null references public.users(id) on delete cascade,
  weekday smallint not null check (weekday between 0 and 6),
  start_min smallint not null check (start_min between 0 and 1439),
  end_min smallint not null check (end_min between 1 and 1440),
  primary key (user_id,weekday,start_min),
  check (end_min > start_min)
);

-- Replace the whole pattern at once, so a failed write never leaves half of it.
create or replace function public.amifree_replace_usual(p_user_id uuid, p_windows jsonb)
returns void language plpgsql set search_path = public, pg_temp as $$
begin
  if jsonb_typeof(p_windows) <> 'array' or jsonb_array_length(p_windows) > 28 then
    raise exception 'Invalid windows';
  end if;
  perform 1 from public.users where id=p_user_id for update;
  if not found then raise exception 'Unknown user'; end if;
  delete from public.weekly_availability where user_id=p_user_id;
  insert into public.weekly_availability(user_id,weekday,start_min,end_min)
  select p_user_id,(w->>'weekday')::smallint,(w->>'start_min')::smallint,(w->>'end_min')::smallint
  from jsonb_array_elements(p_windows) w
  on conflict do nothing;
end $$;

alter table public.pro_payments add column if not exists refunded_at timestamptz;

alter table public.weekly_availability enable row level security;
revoke all on public.weekly_availability from anon,authenticated;
grant all on public.weekly_availability to service_role;
revoke all on function public.amifree_replace_usual(uuid,jsonb) from public,anon,authenticated;
grant execute on function public.amifree_replace_usual(uuid,jsonb) to service_role;

insert into public.schema_migrations(version,name) values (11,'usual_times_and_refunds')
on conflict (version) do nothing;
commit;

-- ===== 012_deadlines.sql =====
-- Apply once after 011; safe to reapply (and safe if an earlier copy named 008_deadlines was
-- already applied). Personal deadlines ("CS3244 report due Fri 11:59pm")
-- and their reminders. A deadline never makes anyone busy.
begin;
create table if not exists public.deadlines (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null check (length(title) between 1 and 200),
  due_at timestamptz not null,
  notes text check (notes is null or length(notes) <= 1000),
  done boolean not null default false,
  source_message_id bigint,
  created_at timestamptz not null default now(),
  unique (user_id, source_message_id)
);
create index if not exists deadlines_due on public.deadlines(due_at) where not done;
create index if not exists deadlines_user on public.deadlines(user_id, due_at);
alter table public.deadlines enable row level security;
revoke all on public.deadlines from anon,authenticated;
grant all on public.deadlines to service_role;

alter table public.notification_logs drop constraint if exists notification_logs_kind_check;
alter table public.notification_logs add constraint notification_logs_kind_check
  -- Every kind any migration has added: the last one applied must not drop the others.
  check (kind in ('NIGHTLY','CHANGE','NUDGE','CONFIRM','DEADLINE'));

insert into public.schema_migrations(version,name) values (12,'deadlines')
on conflict (version) do nothing;
commit;

-- ===== 013_reminders.sql =====
-- Apply once after 012; safe to reapply. Quick reminders ("remind me at 5pm to buy milk"):
-- a message that arrives at an exact time. A reminder never makes anyone busy.
begin;
create table if not exists public.reminders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  text text not null check (length(text) between 1 and 200),
  remind_at timestamptz not null,
  done boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists reminders_due on public.reminders(remind_at) where not done;
create index if not exists reminders_user on public.reminders(user_id, remind_at);
alter table public.reminders enable row level security;
revoke all on public.reminders from anon,authenticated;
grant all on public.reminders to service_role;

alter table public.notification_logs drop constraint if exists notification_logs_kind_check;
alter table public.notification_logs add constraint notification_logs_kind_check
  -- Every kind any migration has added: the last one applied must not drop the others.
  check (kind in ('NIGHTLY','CHANGE','NUDGE','CONFIRM','DEADLINE','REMIND'));

insert into public.schema_migrations(version,name) values (13,'reminders')
on conflict (version) do nothing;
commit;

-- ===== 014_suggestion_author.sql =====
-- Apply once after 013; safe to reapply. A detection suggestion remembers who wrote the
-- message, so only that person or a group admin can turn it into a card or ignore it.
-- A Telegram id, not a user row: the author may never have used AmIFree, and suggestions are
-- removed after 10 minutes. Rows from before this migration have no author and stay open to all.
begin;
alter table public.event_suggestions add column if not exists author_telegram_id bigint;

insert into public.schema_migrations(version,name) values (14,'suggestion_author')
on conflict (version) do nothing;
commit;

-- ===== 015_google_calendar.sql =====
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

-- ===== 016_feedback.sql =====
-- Apply once after 015; safe to reapply. Anonymous-by-default user feedback: the message, a
-- status to triage, and a username/name only when the person chose to send it with their handle.
begin;
create table if not exists public.feedback (
  id uuid primary key default gen_random_uuid(),
  message text not null check (length(message) between 1 and 2000),
  status text not null default 'new' check (status in ('new','read','done')),
  username text check (username is null or length(username) <= 64),
  name text check (name is null or length(name) <= 128),
  created_at timestamptz not null default now()
);
create index if not exists feedback_recent on public.feedback(created_at desc);
alter table public.feedback enable row level security;
revoke all on public.feedback from anon,authenticated;
grant all on public.feedback to service_role;

insert into public.schema_migrations(version,name) values (16,'feedback')
on conflict (version) do nothing;
commit;

-- ===== 017_plans_days.sql =====
-- Apply once after 016; safe to reapply. How many days ahead /plans writes out (the rest sit
-- behind its Later plans button).
begin;
alter table public.users add column if not exists plans_days integer not null default 7;
alter table public.users drop constraint if exists users_plans_days_check;
alter table public.users add constraint users_plans_days_check check (plans_days in (3,7,14,30));

insert into public.schema_migrations(version,name) values (17,'plans_days')
on conflict (version) do nothing;
commit;
