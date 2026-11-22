-- Table 6: feature use, up to the end of 20 Nov 2026. Read-only.
-- "Uses" counts logged actions; a plan sent twice by Telegram counts once. Group event cards come
-- from the group events table, so each session of a recurring event counts.
with cut as (select timestamptz '2026-11-21 00:00+08' as ts),
acts as (
  select a.user_id, a.action_type, a.metadata,
    case
      when a.action_type = 'forward' then 'Check a plan'
      when a.action_type in ('group_going', 'group_maybe', 'group_declined') then 'Group RSVP'
      when a.action_type in ('going', 'maybe', 'declined') then 'RSVP to own plan'
      when a.action_type in ('deadline', 'deadline_done', 'deadline_edit') then 'Deadlines'
      when a.action_type = 'edited' then 'Edit a plan'
      when a.action_type in ('calendar_feed_created', 'calendar_export') then 'Calendar feed link'
      when a.action_type = 'poll' then 'Create a poll (Find a time)'
    end as feature
  from public.user_actions a, cut
  where a.created_at < cut.ts
)
select feature,
       case when feature in ('Group RSVP', 'Create a poll (Find a time)') then 'Group' else 'Individual' end as type,
       count(distinct user_id) as users,
       case when feature = 'Check a plan' then count(distinct (user_id, metadata->>'message_id')) else count(*) end as uses
from acts
where feature is not null
group by feature
union all
select 'Group event cards created', 'Group', count(distinct creator_user_id), count(*)
from public.group_events g, cut
where g.created_at < cut.ts
order by uses desc;
