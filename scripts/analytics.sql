-- Read-only, aggregate pilot metrics. Run as an operator in Supabase SQL Editor.
-- Forward/correction attempts deduplicate retries by original Telegram message ID.
-- Basic analytics are best-effort; deleted users' activity is also deleted.

-- Unique users who started and subsequently completed setup (all time).
with starters as (
  select distinct user_id from public.user_actions where action_type='start'
), completed as (
  select distinct user_id from public.user_actions where action_type='onboarding_complete'
)
select count(*) as started_users,
  count(completed.user_id) as onboarded_users,
  round(100.0*count(completed.user_id)/nullif(count(*),0),1) as onboarding_percent
from starters left join completed using(user_id);

-- Activity and correction rate over the last 14 days.
with forwards as (
  select distinct user_id,metadata->>'message_id' as message_id
  from public.user_actions
  where action_type='forward' and created_at>=now()-interval '14 days'
), corrections as (
  select distinct user_id,metadata->>'message_id' as message_id
  from public.user_actions
  where action_type='correction' and created_at>=now()-interval '14 days'
)
select count(*) as forwarded_events,count(distinct forwards.user_id) as active_users,
  round(count(*)::numeric/nullif(count(distinct forwards.user_id),0),2) as forwards_per_active_user,
  count(corrections.user_id) as corrected_events,
  round(100.0*count(corrections.user_id)/nullif(count(*),0),1) as correction_percent
from forwards left join corrections using(user_id,message_id);

-- Result quality, RSVP response rate and server processing latency in the last 14 days.
with verdicts as (
  select distinct on (event_id) event_id,metadata
  from public.user_actions where action_type='verdict' and created_at>=now()-interval '14 days'
  order by event_id,created_at
), rsvps as (
  select distinct event_id from public.user_actions
  where action_type in ('going','maybe','declined') and created_at>=now()-interval '14 days'
)
select count(*) as event_results,
  count(*) filter(where metadata->>'verdict'='CLASH') as clashes_detected,
  count(*) filter(where metadata->>'verdict'='UNKNOWN') as outside_coverage,
  round(100.0*count(rsvps.event_id)/nullif(count(*),0),1) as rsvp_percent,
  percentile_cont(0.5) within group(order by (metadata->>'latency_ms')::numeric) as median_processing_ms
from verdicts left join rsvps using(event_id);

-- Week-two retention: users who forward during both first and second seven-day windows.
-- Exclude users whose full two-week observation window hasn't elapsed.
with first_forward as (
  select user_id,min(created_at) as first_at from public.user_actions where action_type='forward' group by user_id
), eligible as (
  select * from first_forward where first_at<=now()-interval '14 days'
), returned as (
  select distinct eligible.user_id from eligible join public.user_actions a using(user_id)
  where a.action_type='forward' and a.created_at>=first_at+interval '7 days' and a.created_at<first_at+interval '14 days'
)
select count(*) as eligible_users,count(returned.user_id) as week_two_repeat_users,
  round(100.0*count(returned.user_id)/nullif(count(*),0),1) as repeat_percent
from eligible left join returned using(user_id);
