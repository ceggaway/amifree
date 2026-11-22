-- Table 5: calendar connection by signup week (Monday to Sunday, Singapore time), up to the end of 20 Nov 2026.
-- Read-only. The first two weeks are combined, as in the report.
with cut as (select timestamptz '2026-11-21 00:00+08' as ts),
connected as (
  select id as user_id from public.users where onboarding_status = 'READY'   -- NUSMods
  union select user_id from public.personal_calendars                        -- calendar links
  union select user_id from public.google_connections                        -- Google sharing
),
signups as (
  select date_trunc('week', u.created_at at time zone 'Asia/Singapore')::date as week,
         exists (select 1 from connected c where c.user_id = u.id) as has_calendar
  from public.users u, cut
  where u.created_at < cut.ts
)
select case when week < date '2026-10-19' then '5–18 Oct' else to_char(week, 'FMDD Mon') end as signup_week,
       count(*) as users,
       count(*) filter (where has_calendar) as connected_a_calendar,
       round(100.0 * count(*) filter (where has_calendar) / count(*)) as percent
from signups
group by 1
order by min(week);
