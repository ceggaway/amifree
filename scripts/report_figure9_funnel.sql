-- Figure 9: students at each step, up to the end of 20 Nov 2026 (Singapore time). Read-only.
with cut as (select timestamptz '2026-11-21 00:00+08' as ts),
acts as (   -- what users did themselves: not /start, bot prompts or team announcements
  select a.user_id, a.action_type
  from public.user_actions a, cut
  where a.created_at < cut.ts
    and a.action_type not in ('start', 'calendar_prompt')
    and a.action_type not like 'announcement:%'
),
connected as (
  select id as user_id from public.users where onboarding_status = 'READY'   -- NUSMods
  union select user_id from public.personal_calendars                        -- calendar links
  union select user_id from public.google_connections                        -- Google sharing
)
select 1 as ord, 'Started the bot' as step,
       (select count(*) from public.users u, cut where u.created_at < cut.ts) as students
union all
select 2, 'Active users', (select count(distinct user_id) from acts)
union all
select 3, 'Took a useful action',
       (select count(distinct user_id) from acts
        where action_type in ('forward', 'going', 'maybe', 'declined', 'group_going', 'group_maybe',
                              'group_declined', 'deadline', 'deadline_done', 'deadline_edit', 'edited',
                              'poll', 'calendar_feed_created', 'calendar_export', 'group_event', 'group_series'))
union all
select 4, 'Connected a calendar',
       (select count(distinct c.user_id) from connected c
        join public.users u on u.id = c.user_id, cut where u.created_at < cut.ts)
order by ord;
