-- Apply once after 017; safe to reapply. Separate switches for the evening summary (and its
-- hour) and deadline reminders. reminders_enabled now covers group check-ins and nudges only;
-- anyone who had turned it off keeps everything off.
begin;
do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'users' and column_name = 'deadline_alerts'
  ) then
    alter table public.users add column summary_enabled boolean not null default true;
    alter table public.users add column summary_hour smallint
      check (summary_hour is null or summary_hour between 18 and 23);
    alter table public.users add column deadline_alerts boolean not null default true;
    update public.users set summary_enabled = false, deadline_alerts = false
      where not reminders_enabled;
  end if;
end $$;

insert into public.schema_migrations(version,name) values (18,'reminder_settings')
on conflict (version) do nothing;
commit;
