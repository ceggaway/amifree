-- Figure 10: new users per week (Monday to Sunday, Singapore time), up to the end of 20 Nov 2026.
-- Read-only. Run in the Supabase SQL Editor; the Chart tab draws the bars.
select date_trunc('week', created_at at time zone 'Asia/Singapore')::date as week,
       count(*) as signups
from public.users
where created_at < timestamptz '2026-11-21 00:00+08'
group by 1
order by 1;
