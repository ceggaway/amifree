-- Apply after 020; safe to reapply. Explicit dates; NULL preserves existing date ranges.
begin;
alter table public.polls add column if not exists selected_dates date[];
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid='public.polls'::regclass and conname='polls_selected_dates_check') then
    alter table public.polls add constraint polls_selected_dates_check check (
      selected_dates is null or (
        cardinality(selected_dates) between 1 and 14
        and array_position(selected_dates, null) is null
      )
    );
  end if;
end $$;
-- Replace the old signature with a backwards-compatible default argument; avoid RPC overload ambiguity.
drop function if exists public.amifree_create_poll(uuid,bigint,bigint,text,integer,timestamptz,timestamptz);
create or replace function public.amifree_create_poll(
  p_user_id uuid,p_chat_id bigint,p_source_message_id bigint,p_title text,
  p_duration integer,p_window_start timestamptz,p_window_end timestamptz,
  p_selected_dates date[] default null
) returns setof public.polls language plpgsql set search_path = public, pg_temp as $$
declare created public.polls; chosen date[];
begin
  perform 1 from public.users where id=p_user_id for update;
  if not found then raise exception 'Unknown user'; end if;
  if p_selected_dates is not null then
    if cardinality(p_selected_dates) not between 1 and 14 or array_position(p_selected_dates,null) is not null then
      raise exception 'Choose between 1 and 14 dates';
    end if;
    select array_agg(distinct d order by d) into chosen from unnest(p_selected_dates) d;
    if exists(select 1 from unnest(chosen) d where d < (p_window_start at time zone 'Asia/Singapore')::date
      or d >= (p_window_end at time zone 'Asia/Singapore')::date) then
      raise exception 'Selected date outside poll window';
    end if;
    if chosen[cardinality(chosen)] - chosen[1] >= 14 then raise exception 'Choose dates within 14 days'; end if;
  end if;
  insert into public.group_members(chat_id,user_id) values(p_chat_id,p_user_id) on conflict do nothing;
  insert into public.polls(chat_id,source_message_id,creator_user_id,title,duration_minutes,window_start,window_end,selected_dates)
  values(p_chat_id,p_source_message_id,p_user_id,p_title,p_duration,p_window_start,p_window_end,chosen)
  on conflict(chat_id,source_message_id) do nothing returning * into created;
  if found then
    insert into public.poll_participants(poll_id,user_id) values(created.id,p_user_id);
  end if;
  return query select * from public.polls where chat_id=p_chat_id and source_message_id=p_source_message_id;
end $$;
revoke all on function public.amifree_create_poll(uuid,bigint,bigint,text,integer,timestamptz,timestamptz,date[]) from public,anon,authenticated;
grant execute on function public.amifree_create_poll(uuid,bigint,bigint,text,integer,timestamptz,timestamptz,date[]) to service_role;
insert into public.schema_migrations(version,name) values(21,'poll_selected_dates') on conflict(version) do nothing;
commit;
