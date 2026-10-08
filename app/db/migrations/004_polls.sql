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
