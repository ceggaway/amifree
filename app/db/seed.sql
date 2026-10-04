-- OPTIONAL DEVELOPMENT FIXTURE ONLY. Never needed for normal onboarding.
-- Replaces only synthetic Telegram users 999000001 and 999000002.
-- Fixed dates make tests reproducible; they are not the operator's real timetable.
begin;
do $$
declare student public.users; other_student public.users; dinner public.events;
begin
  delete from public.users where telegram_user_id in (999000001,999000002);
  select * into student from public.amifree_ensure_user(999000001);
  select * into other_student from public.amifree_ensure_user(999000002);
  perform public.amifree_import_timetable(student.id,
    '[{"title":"Fixture CS3216 Lecture","start_at":"2026-10-09T20:00:00+08:00","end_at":"2026-10-09T22:00:00+08:00","location":"COM1"}]'::jsonb,
    '2026-2027',1,'2026-08-10T00:00:00+08:00','2026-12-14T00:00:00+08:00');
  perform public.amifree_import_timetable(other_student.id,'[]'::jsonb,
    '2026-2027',1,'2026-08-10T00:00:00+08:00','2026-12-14T00:00:00+08:00');
  select * into dinner from public.amifree_create_preview(student.id,100,
    '{"title":"Fixture CCA dinner","start_at":"2026-10-09T19:00:00+08:00","end_at":"2026-10-09T21:00:00+08:00","location":"Vivo","notes":"Synthetic seed event"}'::jsonb);
  perform public.amifree_rsvp(student.id,dinner.id,'GOING',dinner.version);
  insert into public.user_actions(user_id,action_type) values(student.id,'start'),(other_student.id,'start');
  insert into public.user_actions(user_id,action_type,metadata)
    values(student.id,'forward','{"message_id":100}');
  insert into public.user_actions(user_id,event_id,action_type,metadata)
    values(student.id,dinner.id,'verdict','{"verdict":"CLASH","latency_ms":1250}');
end $$;
commit;
