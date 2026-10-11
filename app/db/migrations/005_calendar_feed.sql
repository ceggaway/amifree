-- Apply once after 004; safe to reapply. Private calendar feed: the user subscribes once
-- (Apple Calendar, later Google) and their Going/Saved plans appear and update by themselves.
begin;
alter table public.users add column if not exists calendar_feed_token text;
-- When their calendar app last fetched the feed: proof the subscription is live.
alter table public.users add column if not exists calendar_feed_seen_at timestamptz;
create unique index if not exists users_calendar_feed_token
  on public.users(calendar_feed_token) where calendar_feed_token is not null;
alter table public.users drop constraint if exists users_calendar_feed_token_length;
alter table public.users add constraint users_calendar_feed_token_length
  check (calendar_feed_token is null or length(calendar_feed_token) >= 32);
commit;
