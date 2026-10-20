-- Apply once after 015; safe to reapply. Anonymous-by-default user feedback: the message, a
-- status to triage, and a username/name only when the person chose to send it with their handle.
begin;
create table if not exists public.feedback (
  id uuid primary key default gen_random_uuid(),
  message text not null check (length(message) between 1 and 2000),
  status text not null default 'new' check (status in ('new','read','done')),
  username text check (username is null or length(username) <= 64),
  name text check (name is null or length(name) <= 128),
  created_at timestamptz not null default now()
);
create index if not exists feedback_recent on public.feedback(created_at desc);
alter table public.feedback enable row level security;
revoke all on public.feedback from anon,authenticated;
grant all on public.feedback to service_role;

insert into public.schema_migrations(version,name) values (16,'feedback')
on conflict (version) do nothing;
commit;
