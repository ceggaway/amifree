-- Apply once after 002; safe to reapply. Durable outbox for reminders, alerts and nudges.
begin;
alter table public.users add column if not exists reminders_enabled boolean not null default true;
-- Set when Telegram refuses a DM (the user blocked the bot or never opened a private chat).
alter table public.users add column if not exists dm_blocked boolean not null default false;

create table if not exists public.notification_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  kind text not null check (kind in ('NIGHTLY','CHANGE','NUDGE')),
  -- With user and kind, identifies the one message that may ever be sent for this cause.
  dedupe_key text not null,
  payload jsonb not null default '{}',
  status text not null default 'pending' check (status in ('pending','sending','sent','failed')),
  attempts integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  claimed_at timestamptz, sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique(user_id,kind,dedupe_key)
);
create index if not exists notifications_due on public.notification_logs(next_attempt_at)
  where status in ('pending','sending');

create or replace function public.amifree_enqueue_notification(
  p_user_id uuid,p_kind text,p_key text,p_payload jsonb
) returns boolean language plpgsql set search_path = public, pg_temp as $$
begin
  insert into public.notification_logs(user_id,kind,dedupe_key,payload)
    values(p_user_id,p_kind,p_key,coalesce(p_payload,'{}'))
    on conflict(user_id,kind,dedupe_key) do nothing;
  return found;
end $$;

-- Claim due rows for one worker. A row left in 'sending' by a crashed worker is
-- reclaimed after ten minutes; that is the only case that can repeat a message.
create or replace function public.amifree_claim_notifications(p_limit integer default 50)
returns setof public.notification_logs language plpgsql set search_path = public, pg_temp as $$
begin
  return query
  update public.notification_logs n set status='sending',attempts=n.attempts+1,claimed_at=now()
  where n.id in (
    select c.id from public.notification_logs c
    where (c.status='pending' and c.next_attempt_at<=now())
       or (c.status='sending' and c.claimed_at<now()-interval '10 minutes')
    order by c.created_at limit greatest(1,least(p_limit,200)) for update skip locked
  )
  returning n.*;
end $$;

-- Mark sent only after Telegram confirmed. Failures back off, then give up after 5 tries.
create or replace function public.amifree_finish_notification(p_id uuid,p_ok boolean,p_permanent boolean)
returns void language sql set search_path = public, pg_temp as $$
  update public.notification_logs set
    status=case when p_ok then 'sent' when p_permanent or attempts>=5 then 'failed' else 'pending' end,
    sent_at=case when p_ok then now() else sent_at end,
    next_attempt_at=now()+make_interval(mins=>power(2,least(attempts,10))::integer)
  where id=p_id and status='sending';
$$;

create or replace function public.amifree_update_group_event(
  p_user_id uuid,p_group_event_id uuid,p_draft jsonb,p_version integer,p_cancel boolean
) returns setof public.group_events language plpgsql set search_path = public, pg_temp as $$
declare old public.group_events; changed public.group_events;
begin
  -- Only the organiser may change a shared card, and only from its latest version.
  select * into old from public.group_events
    where id=p_group_event_id and creator_user_id=p_user_id and version=p_version and status='ACTIVE'
    for update;
  if not found then return; end if;
  update public.group_events set
    title=case when p_cancel then title else p_draft->>'title' end,
    start_at=case when p_cancel then start_at else (p_draft->>'start_at')::timestamptz end,
    end_at=case when p_cancel then end_at else (p_draft->>'end_at')::timestamptz end,
    location=case when p_cancel then location else p_draft->>'location' end,
    notes=case when p_cancel then notes else p_draft->>'notes' end,
    assumed_duration=case when p_cancel then assumed_duration else coalesce((p_draft->>'assumed_duration')::boolean,false) end,
    status=case when p_cancel then 'CANCELLED' else status end,
    version=version+1,updated_at=now()
  where id=old.id returning * into changed;
  insert into public.event_changes(group_event_id,version,before,after,changed_by)
    values(changed.id,changed.version,to_jsonb(old),to_jsonb(changed),p_user_id);
  -- One alert per member who kept the plan, queued in the same transaction as the change.
  -- Runs before linked rows are cancelled so their answers are still visible here.
  insert into public.notification_logs(user_id,kind,dedupe_key,payload)
  select e.user_id,'CHANGE',changed.id::text||':'||changed.version,
    jsonb_build_object('group_event_id',changed.id,'event_id',e.id,'cancelled',p_cancel,
      'before',to_jsonb(old),'after',to_jsonb(changed))
  from public.events e
  where e.group_event_id=changed.id and e.user_id<>p_user_id and e.status in ('GOING','MAYBE','SAVED')
  on conflict(user_id,kind,dedupe_key) do nothing;
  update public.events set title=changed.title,start_at=changed.start_at,end_at=changed.end_at,
    location=changed.location,notes=changed.notes,assumed_duration=changed.assumed_duration,
    status=case when p_cancel then 'CANCELLED' else status end,
    version=version+1,updated_at=now()
  where group_event_id=changed.id and status<>'CANCELLED';
  return next changed;
end $$;

alter table public.notification_logs enable row level security;
revoke all on public.notification_logs from anon,authenticated;
grant all on public.notification_logs to service_role;
revoke all on function public.amifree_enqueue_notification(uuid,text,text,jsonb),
  public.amifree_claim_notifications(integer),
  public.amifree_finish_notification(uuid,boolean,boolean) from public,anon,authenticated;
grant execute on function public.amifree_enqueue_notification(uuid,text,text,jsonb),
  public.amifree_claim_notifications(integer),
  public.amifree_finish_notification(uuid,boolean,boolean) to service_role;
commit;
