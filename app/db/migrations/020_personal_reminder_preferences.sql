-- Apply after 019; safe to reapply. Personal reminder timing preferences.
-- Apply after 019. Reapplying retains saved preferences and never re-enables a reminder.
begin;
do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema='public' and table_name='users' and column_name='overview_enabled'
  ) then
    alter table public.users
      add column overview_enabled boolean not null default true,
      add column overview_period text not null default 'morning'
        check (overview_period in ('morning','evening')),
      add column overview_morning_minute smallint not null default 540
        check (overview_morning_minute between 0 and 719),
      add column overview_evening_minute smallint not null default 1260
        check (overview_evening_minute between 1080 and 1439),
      add column deadline_lead_minutes integer not null default 180
        check (deadline_lead_minutes between 1 and 43200);
    update public.users set
      overview_enabled = summary_enabled or morning_enabled,
      overview_period = case when morning_enabled then 'morning' else 'evening' end,
      overview_evening_minute = coalesce(summary_hour * 60, 1260);
    -- A morning overview already delivered today must not be sent again after switching.
    insert into public.notification_logs(user_id,kind,dedupe_key,payload,status,sent_at)
      select user_id,'NIGHTLY',dedupe_key,payload,'sent',sent_at
      from public.notification_logs where kind='TODAY' and status='sent'
      on conflict(user_id,kind,dedupe_key) do nothing;
    -- Retire queued legacy summaries: the scheduler rebuilds them from current preferences.
    delete from public.notification_logs
      where kind in ('TODAY','NIGHTLY') and status in ('pending','sending');
  end if;
end $$;
insert into public.schema_migrations(version,name) values (20,'personal_reminder_preferences')
on conflict(version) do nothing;
commit;
