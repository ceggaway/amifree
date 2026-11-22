-- Section 8 metrics for the final report. READ-ONLY: it only runs SELECTs.
-- Paste into the Supabase SQL Editor, run, and copy the single JSON cell it returns.
-- Each metric is computed twice: up to 15 Nov 00:00 SGT (the report's old cut-off,
-- to check definitions against the published numbers) and up to now.
with cut(label, ts) as (
  values ('to_15_nov', timestamptz '2026-11-15 00:00+08'), ('now', now())
),
u as (select id, created_at from public.users),
-- Everything a user did after /start.
a as (
  select user_id, event_id, action_type, metadata, created_at
  from public.user_actions where action_type <> 'start'
),
-- Calendar connections. NUSMods import has no timestamp of its own, so it counts as
-- connected at any cut-off; links and Google carry their own dates.
conn as (
  select id as user_id, 'nusmods' as src, null::timestamptz as at_ from public.users where onboarding_status = 'READY'
  union all select user_id, 'ics', created_at from public.personal_calendars
  union all select user_id, 'google', connected_at from public.google_connections
),
first_verdict as (
  select distinct on (event_id) event_id, user_id, metadata, created_at
  from public.user_actions
  where action_type = 'verdict' and event_id is not null
  order by event_id, created_at
)
select jsonb_object_agg(c.label, jsonb_build_object(
  'started',
    (select count(*) from u where u.created_at < c.ts),
  'active_any_action',
    (select count(distinct user_id) from a where a.created_at < c.ts),
  'connected_any',
    (select count(distinct conn.user_id) from conn join u on u.id = conn.user_id
      where u.created_at < c.ts and (conn.at_ is null or conn.at_ < c.ts)),
  'connected_by_source',
    (select jsonb_object_agg(src, n) from (
      select src, count(distinct user_id) as n from conn
      where conn.at_ is null or conn.at_ < c.ts group by src) s),
  'action_types_users_rows',
    (select jsonb_object_agg(action_type, jsonb_build_array(users, rows_)) from (
      select action_type, count(distinct user_id) as users, count(*) as rows_
      from public.user_actions where created_at < c.ts group by action_type) s),
  'top_signup_days',
    (select jsonb_agg(jsonb_build_array(d, n) order by n desc, d) from (
      select (created_at at time zone 'Asia/Singapore')::date as d, count(*) as n
      from u where u.created_at < c.ts group by 1 order by 2 desc, 1 limit 6) s),
  'signup_weeks',
    (select jsonb_agg(jsonb_build_object('week', w, 'users', n, 'connected', k) order by w) from (
      select w, count(*) as n, count(*) filter (where linked) as k from (
        select date_trunc('week', u.created_at at time zone 'Asia/Singapore')::date as w,
          exists (select 1 from conn where conn.user_id = u.id and (conn.at_ is null or conn.at_ < c.ts)) as linked
        from u where u.created_at < c.ts) x
      group by w) s),
  'active_days',
    (select jsonb_build_object('one_day', count(*) filter (where days = 1), 'two_plus', count(*) filter (where days >= 2)) from (
      select user_id, count(distinct (created_at at time zone 'Asia/Singapore')::date) as days
      from a where a.created_at < c.ts group by user_id) s),
  'retention_back_eligible',
    (select jsonb_object_agg(k, jsonb_build_array(back, eligible)) from (
      select k, count(*) as eligible, count(*) filter (where came_back) as back from (
        select kk.k, exists (
            select 1 from a where a.user_id = u.id
              and a.created_at >= u.created_at + make_interval(days => kk.k)
              and a.created_at < c.ts) as came_back
        from u cross join (values (1), (7), (14)) as kk(k)
        where u.created_at <= c.ts - make_interval(days => kk.k)) x
      group by k) s),
  'weekly_active',
    (select jsonb_agg(jsonb_build_array(w, n) order by w) from (
      select date_trunc('week', created_at at time zone 'Asia/Singapore')::date as w, count(distinct user_id) as n
      from a where a.created_at < c.ts group by 1) s),
  'verdicts',
    (select jsonb_object_agg(v, n) from (
      select metadata->>'verdict' as v, count(*) as n
      from first_verdict where created_at < c.ts group by 1) s),
  'unknown_with_calendar_without',
    (select jsonb_build_array(
        count(*) filter (where linked), count(*) filter (where not linked)) from (
      select exists (select 1 from conn where conn.user_id = f.user_id
          and (conn.at_ is null or conn.at_ < f.created_at)) as linked
      from first_verdict f
      where f.created_at < c.ts and f.metadata->>'verdict' = 'UNKNOWN') x),
  'check_latency_ms_p50_p90',
    (select jsonb_build_array(
        percentile_cont(0.5) within group (order by (metadata->>'latency_ms')::numeric),
        percentile_cont(0.9) within group (order by (metadata->>'latency_ms')::numeric))
      from a where a.action_type = 'verdict' and a.metadata ? 'latency_ms' and a.created_at < c.ts),
  'plan_messages_deduplicated',
    (select count(*) from (
      select distinct user_id, metadata->>'message_id'
      from a where a.action_type = 'forward' and a.created_at < c.ts) s),
  'group_events',
    (select jsonb_build_object('events', count(*), 'organisers', count(distinct creator_user_id), 'chats', count(distinct chat_id))
      from public.group_events g where g.created_at < c.ts),
  'notifications_by_status',
    (select jsonb_object_agg(status, n) from (
      select status, count(*) as n from public.notification_logs n where n.created_at < c.ts group by 1) s),
  'last_action_at',
    (select max(created_at) from public.user_actions x where x.created_at < c.ts)
)) as section8
from cut c;
