-- Apply once after 012; safe to reapply. Quick reminders ("remind me at 5pm to buy milk"):
-- a message that arrives at an exact time. A reminder never makes anyone busy.
begin;
create table if not exists public.reminders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  text text not null check (length(text) between 1 and 200),
  remind_at timestamptz not null,
  done boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists reminders_due on public.reminders(remind_at) where not done;
create index if not exists reminders_user on public.reminders(user_id, remind_at);
alter table public.reminders enable row level security;
revoke all on public.reminders from anon,authenticated;
grant all on public.reminders to service_role;

alter table public.notification_logs drop constraint if exists notification_logs_kind_check;
alter table public.notification_logs add constraint notification_logs_kind_check
  -- Every kind any migration has added: the last one applied must not drop the others.
  check (kind in ('NIGHTLY','CHANGE','NUDGE','CONFIRM','DEADLINE','REMIND'));

insert into public.schema_migrations(version,name) values (13,'reminders')
on conflict (version) do nothing;
commit;
