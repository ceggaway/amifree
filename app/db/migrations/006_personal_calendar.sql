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
