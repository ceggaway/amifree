-- Apply once after 016; safe to reapply. Which calendar app subscribed to the AmIFree calendar
-- (from its fetches), so /calendar can list it under Apple, Outlook or Google.
begin;
alter table public.users add column if not exists calendar_feed_app text
  check (calendar_feed_app is null or calendar_feed_app in ('apple','outlook','google'));

insert into public.schema_migrations(version,name) values (17,'calendar_feed_app')
on conflict (version) do nothing;
commit;
