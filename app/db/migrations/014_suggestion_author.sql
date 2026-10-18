-- Apply once after 013; safe to reapply. A detection suggestion remembers who wrote the
-- message, so only that person or a group admin can turn it into a card or ignore it.
-- A Telegram id, not a user row: the author may never have used AmIFree, and suggestions are
-- removed after 10 minutes. Rows from before this migration have no author and stay open to all.
begin;
alter table public.event_suggestions add column if not exists author_telegram_id bigint;

insert into public.schema_migrations(version,name) values (14,'suggestion_author')
on conflict (version) do nothing;
commit;
