-- Apply once after 016; safe to reapply. How many days ahead /plans writes out (the rest sit
-- behind its Later plans button).
begin;
alter table public.users add column if not exists plans_days integer not null default 7;
alter table public.users drop constraint if exists users_plans_days_check;
alter table public.users add constraint users_plans_days_check check (plans_days in (3,7,14,30));

insert into public.schema_migrations(version,name) values (17,'plans_days')
on conflict (version) do nothing;
commit;
