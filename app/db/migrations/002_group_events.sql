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
