-- Apply once after 007; safe to reapply. Adds a migration ledger (the app checks it at
-- startup) and durable Telegram update de-duplication, so a webhook retry after a restart
-- cannot repeat a save, RSVP or delete.
begin;

create table if not exists public.schema_migrations (
  version integer primary key,
  name text not null,
  applied_at timestamptz not null default now()
);
-- Migrations 001-007 predate the ledger and are verified by object checks. From here on every
-- migration records itself, and the app refuses to start below the version it was built for.
insert into public.schema_migrations(version, name)
values (8, 'schema_ledger_and_processed_updates')
on conflict (version) do nothing;

create table if not exists public.processed_updates (
  update_id bigint primary key,
  status text not null default 'processing' check (status in ('processing', 'done')),
  claimed_at timestamptz not null default now()
);
create index if not exists processed_updates_claimed on public.processed_updates(claimed_at);

-- True when this caller should process the update: it is new, or an earlier attempt was
-- claimed but never finished and is older than p_stale_seconds (it probably crashed).
create or replace function public.amifree_claim_update(
  p_update_id bigint, p_stale_seconds integer default 120
) returns boolean language plpgsql set search_path = public, pg_temp as $$
declare claimed boolean;
begin
  delete from public.processed_updates where claimed_at < now() - interval '7 days';
  insert into public.processed_updates(update_id) values (p_update_id)
  on conflict (update_id) do update set claimed_at = now()
    where public.processed_updates.status = 'processing'
      and public.processed_updates.claimed_at < now() - make_interval(secs => p_stale_seconds)
  returning true into claimed;
  return coalesce(claimed, false);
end $$;

create or replace function public.amifree_finish_update(p_update_id bigint)
returns void language sql set search_path = public, pg_temp as $$
  update public.processed_updates set status = 'done' where update_id = p_update_id;
$$;

-- Lets a retry reprocess an update that failed before anything was committed.
create or replace function public.amifree_release_update(p_update_id bigint)
returns void language sql set search_path = public, pg_temp as $$
  delete from public.processed_updates where update_id = p_update_id and status = 'processing';
$$;

alter table public.schema_migrations enable row level security;
alter table public.processed_updates enable row level security;
revoke all on public.schema_migrations, public.processed_updates from anon, authenticated;
grant all on public.schema_migrations, public.processed_updates to service_role;
revoke all on function public.amifree_claim_update(bigint, integer),
  public.amifree_finish_update(bigint), public.amifree_release_update(bigint)
  from public, anon, authenticated;
grant execute on function public.amifree_claim_update(bigint, integer),
  public.amifree_finish_update(bigint), public.amifree_release_update(bigint) to service_role;
commit;
