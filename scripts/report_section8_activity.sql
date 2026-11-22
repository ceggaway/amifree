-- Section 8 activity metrics, part 2. READ-ONLY: it only runs SELECTs.
-- Counts only what users themselves did: team announcements and bot prompts are left out.
-- Paste into the Supabase SQL Editor, run, and copy the single JSON cell it returns.
with cut(label, ts) as (
  values ('to_15_nov', timestamptz '2026-11-15 00:00+08'), ('now', now())
),
u as (select id, created_at from public.users),
a as (
  select x.user_id, x.action_type, x.metadata, x.created_at,
    case
      when action_type = 'forward' then 'check_plan'
      when action_type in ('going', 'maybe', 'declined') then 'rsvp_own_plan'
      when action_type in ('group_going', 'group_maybe', 'group_declined') then 'group_rsvp'
      when action_type in ('deadline', 'deadline_done', 'deadline_edit') then 'deadlines'
      when action_type = 'edited' then 'edit_plan'
      when action_type = 'poll' then 'create_poll'
      when action_type in ('calendar_feed_created', 'calendar_export') then 'calendar_feed_link'
      when action_type in ('group_event', 'group_series') then 'create_group_event'
      when action_type in ('demo_tried', 'demo_deadline_tried') then 'demo'
      else 'other'
    end as feature,
    action_type in ('forward', 'going', 'maybe', 'declined', 'group_going', 'group_maybe', 'group_declined',
      'deadline', 'deadline_done', 'deadline_edit', 'edited', 'poll', 'calendar_feed_created',
      'calendar_export', 'group_event', 'group_series') as useful,
    action_type in ('group_going', 'group_maybe', 'group_declined', 'poll', 'group_event', 'group_series') as is_group
  from public.user_actions x
  where action_type not in ('start', 'calendar_prompt') and action_type not like 'announcement:%'
)
select jsonb_object_agg(c.label, jsonb_build_object(
  'started', (select count(*) from u where u.created_at < c.ts),
  'active', (select count(distinct user_id) from a where a.created_at < c.ts),
  'active_without_demo', (select count(distinct user_id) from a where a.created_at < c.ts and feature <> 'demo'),
  'useful', (select count(distinct user_id) from a where a.created_at < c.ts and useful),
  'useful_individual_group_both',
    (select jsonb_build_array(count(*) filter (where ind), count(*) filter (where grp), count(*) filter (where ind and grp)) from (
      select user_id, bool_or(useful and not is_group) as ind, bool_or(is_group) as grp
      from a where a.created_at < c.ts and useful group by user_id) s),
  'first_useful_within_24h_and_median_minutes',
    (select jsonb_build_array(
        count(*) filter (where gap <= interval '24 hours'),
        round((percentile_cont(0.5) within group (order by extract(epoch from gap) / 60))::numeric, 1)) from (
      select min(a.created_at) - u.created_at as gap
      from a join u on u.id = a.user_id where a.created_at < c.ts and a.useful group by a.user_id, u.created_at) s),
  'active_days_one_twoplus',
    (select jsonb_build_array(count(*) filter (where days = 1), count(*) filter (where days >= 2)) from (
      select user_id, count(distinct (created_at at time zone 'Asia/Singapore')::date) as days
      from a where a.created_at < c.ts group by user_id) s),
  'weekly_active_and_signed_up_earlier',
    (select jsonb_agg(jsonb_build_array(w, n, earlier) order by w) from (
      select date_trunc('week', a.created_at at time zone 'Asia/Singapore')::date as w,
        count(distinct a.user_id) as n,
        count(distinct a.user_id) filter (
          where u.created_at at time zone 'Asia/Singapore' < date_trunc('week', a.created_at at time zone 'Asia/Singapore')) as earlier
      from a join u on u.id = a.user_id where a.created_at < c.ts group by 1) s),
  'returned_back_eligible',
    (select jsonb_object_agg(k, jsonb_build_array(back, eligible)) from (
      select k, count(*) as eligible, count(*) filter (where came_back) as back from (
        select kk.k, exists (
            select 1 from a where a.user_id = u.id
              and a.created_at >= u.created_at + make_interval(days => kk.k) and a.created_at < c.ts) as came_back
        from u cross join (values (1), (7), (14)) as kk(k)
        where u.created_at <= c.ts - make_interval(days => kk.k)) x
      group by k) s),
  'feature_users_uses',
    (select jsonb_object_agg(feature, jsonb_build_array(users, uses)) from (
      select feature, count(distinct user_id) as users,
        case when feature = 'check_plan' then count(distinct (user_id, metadata->>'message_id')) else count(*) end as uses
      from a where a.created_at < c.ts group by feature) s),
  'poll_participants', (select count(distinct user_id) from public.poll_participants),
  'attendance_checkins', (select count(*) from public.attendance_checks)
)) as section8_activity
from cut c;
