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
