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
