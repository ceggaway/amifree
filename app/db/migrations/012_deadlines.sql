-- Apply once after 011; safe to reapply (and safe if an earlier copy named 008_deadlines was
-- already applied). Personal deadlines ("CS3244 report due Fri 11:59pm")
-- and their reminders. A deadline never makes anyone busy.
begin;
create table if not exists public.deadlines (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null check (length(title) between 1 and 200),
  due_at timestamptz not null,
  notes text check (notes is null or length(notes) <= 1000),
  done boolean not null default false,
  source_message_id bigint,
  created_at timestamptz not null default now(),
  unique (user_id, source_message_id)
);
create index if not exists deadlines_due on public.deadlines(due_at) where not done;
create index if not exists deadlines_user on public.deadlines(user_id, due_at);
alter table public.deadlines enable row level security;
revoke all on public.deadlines from anon,authenticated;
grant all on public.deadlines to service_role;

alter table public.notification_logs drop constraint if exists notification_logs_kind_check;
alter table public.notification_logs add constraint notification_logs_kind_check
  -- Every kind any migration has added: the last one applied must not drop the others.
  check (kind in ('NIGHTLY','CHANGE','NUDGE','CONFIRM','DEADLINE'));

insert into public.schema_migrations(version,name) values (12,'deadlines')
on conflict (version) do nothing;
commit;
