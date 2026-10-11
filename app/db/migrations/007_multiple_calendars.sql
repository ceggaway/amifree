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
