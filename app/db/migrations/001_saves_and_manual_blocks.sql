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
