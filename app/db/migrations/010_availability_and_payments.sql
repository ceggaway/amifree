-- Apply once after 009; safe to reapply. Members' own availability for a poll, and Telegram
-- Stars payments for Clubs Pro.
begin;

-- A member's own answer for a /whosfree poll: the half-hour cells (ISO start times) they are
-- free in. Only cells their schedule leaves open can be ticked; the grid never says why a
-- cell is blocked.
create table if not exists public.poll_availability (
  poll_id uuid not null references public.polls(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  free jsonb not null default '[]',
  updated_at timestamptz not null default now(),
  primary key (poll_id,user_id),
  check (jsonb_typeof(free)='array' and jsonb_array_length(free) <= 800)
);

-- One row per Telegram Stars charge, so a replayed payment update never extends Pro twice.
-- Kept after the user is deleted: it is a financial record, and holds no schedule data.
create table if not exists public.pro_payments (
  charge_id text primary key,
  chat_id bigint not null,
  telegram_id bigint not null,
  stars integer not null check (stars >= 0),
  days integer not null check (days > 0),
  paid_at timestamptz not null default now()
);
create index if not exists pro_payments_chat on public.pro_payments(chat_id,paid_at desc);

alter table public.poll_availability enable row level security;
alter table public.pro_payments enable row level security;
revoke all on public.poll_availability,public.pro_payments from anon,authenticated;
grant all on public.poll_availability,public.pro_payments to service_role;

insert into public.schema_migrations(version,name) values (10,'availability_and_payments')
on conflict (version) do nothing;
commit;
