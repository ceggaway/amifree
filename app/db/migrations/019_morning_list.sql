-- Apply once after 018; safe to reapply. Its own switch for the 9am Today list, which now also
-- lists the day's plans. deadline_alerts keeps the 3-hours-before alert; whoever had deadline
-- reminders off keeps the morning list off too.
begin;
do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'users' and column_name = 'morning_enabled'
  ) then
    alter table public.users add column morning_enabled boolean not null default true;
    update public.users set morning_enabled = false where not deadline_alerts;
  end if;
end $$;

insert into public.schema_migrations(version,name) values (19,'morning_list')
on conflict (version) do nothing;
commit;
