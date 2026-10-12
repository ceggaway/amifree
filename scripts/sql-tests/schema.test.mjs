import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

const db = new PGlite();
const schema = await readFile(new URL('../../app/db/schema.sql', import.meta.url), 'utf8');
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
const migrationDir = new URL('../../app/db/migrations/', import.meta.url);
const migrations = [];
for (const file of (await readdir(migrationDir)).filter(f => f.endsWith('.sql')).sort()) {
  migrations.push(await readFile(new URL(file, migrationDir), 'utf8'));
}
// Schema and every migration are applied twice: reapplication must be safe.
for (let pass = 0; pass < 2; pass++) {
  await db.exec(schema);
  for (const migration of migrations) await db.exec(migration);
}
let count = 0;
async function check(name, fn) {
  await fn(); count++; console.log(`PASS: ${name}`);
}
const q = (sql, values=[]) => db.query(sql, values);
const one = async (sql, values=[]) => (await q(sql, values)).rows[0];
await db.exec('set role service_role');
const user = await one('select * from amifree_ensure_user($1)', [1001]);
const other = await one('select * from amifree_ensure_user($1)', [1002]);
const draft = {title:'Dinner', start_at:'2026-10-09T19:00:00+08:00', end_at:'2026-10-09T21:00:00+08:00', location:'Vivo', notes:null};
const block = {title:'CS3216 Lecture', start_at:'2026-10-09T20:00:00+08:00', end_at:'2026-10-09T22:00:00+08:00', location:'COM1'};
const doImport = blocks => q('select amifree_import_timetable($1,$2,$3,$4,$5,$6)', [user.id,JSON.stringify(blocks),'2026-2027',1,'2026-08-10T00:00:00+08:00','2026-12-14T00:00:00+08:00']);
await check('user identity is unique and onboarding is preserved', async () => {
  await doImport([block]);
  const again = await one('select * from amifree_ensure_user($1)', [1001]);
  assert.equal(again.id,user.id); assert.equal(again.onboarding_status,'READY');
});
const event = await one('select * from amifree_create_preview($1,$2,$3)', [user.id,2,JSON.stringify(draft)]);
await check('duplicate Telegram messages create one event', async () => {
  const repeat = await one('select * from amifree_create_preview($1,$2,$3)', [user.id,2,JSON.stringify(draft)]);
  assert.equal(repeat.id,event.id);
  assert.equal((await one('select count(*)::int n from events')).n,1);
});
await check('cross-user RSVP returns no rows', async () => {
  assert.equal((await q('select * from amifree_rsvp($1,$2,$3,$4)',[other.id,event.id,'GOING',1])).rows.length,0);
});
await check('Going persists and records analytics atomically', async () => {
  const going=await one('select * from amifree_rsvp($1,$2,$3,$4)',[user.id,event.id,'GOING',1]);
  assert.equal(going.status,'GOING'); assert.equal(going.version,2);
  assert.equal((await one("select count(*)::int n from user_actions where action_type='going'")).n,1);
});
await check('stale RSVP callbacks cannot overwrite the event', async () => {
  assert.equal((await q('select * from amifree_rsvp($1,$2,$3,$4)',[user.id,event.id,'DECLINED',1])).rows.length,0);
  assert.equal((await one('select status from events where id=$1',[event.id])).status,'GOING');
});
await check('overlap query finds commitments and half-open boundaries do not clash', async () => {
  assert.equal((await q("select id from events where user_id=$1 and status='GOING' and start_at<$2 and end_at>$3",[user.id,'2026-10-09T22:00:00+08:00','2026-10-09T20:00:00+08:00'])).rows.length,1);
  assert.equal((await q("select id from events where user_id=$1 and status='GOING' and start_at<$2 and end_at>$3",[user.id,'2026-10-09T22:00:00+08:00','2026-10-09T21:00:00+08:00'])).rows.length,0);
});
await check('invalid timetable replacement rolls back original blocks', async () => {
  await assert.rejects(doImport([block,{...block,end_at:block.start_at}]));
  assert.equal((await one('select count(*)::int n from timetable_blocks where user_id=$1',[user.id])).n,1);
});
await check('reimport replaces blocks without duplication', async () => {
  await doImport([block]); await doImport([block]);
  assert.equal((await one('select count(*)::int n from timetable_blocks where user_id=$1',[user.id])).n,1);
});
await check('cross-user update cannot modify an event', async () => {
  assert.equal((await q('select * from amifree_update_event($1,$2,$3,$4,$5)',[other.id,event.id,JSON.stringify(draft),2,false])).rows.length,0);
});
await check('update retains event identity and Going state', async () => {
  const updated=await one('select * from amifree_update_event($1,$2,$3,$4,$5)',[user.id,event.id,JSON.stringify({...draft,start_at:'2026-10-09T20:00:00+08:00',end_at:'2026-10-09T22:00:00+08:00'}),2,false]);
  assert.equal(updated.id,event.id); assert.equal(updated.version,3); assert.equal(updated.status,'GOING');
});
await check('invalid edits roll back status and analytics', async () => {
  await assert.rejects(q('select * from amifree_update_event($1,$2,$3,$4,$5)',[user.id,event.id,JSON.stringify({...draft,end_at:draft.start_at}),3,false]));
  assert.equal((await one('select version from events where id=$1',[event.id])).version,3);
  assert.equal((await one("select count(*)::int n from user_actions where action_type='edited'")).n,1);
});
await check('cancellation clears the commitment without creating another event', async () => {
  const cancelled=await one('select * from amifree_update_event($1,$2,$3,$4,$5)',[user.id,event.id,'{}',3,true]);
  assert.equal(cancelled.status,'CANCELLED');
  assert.equal((await q('select * from amifree_rsvp($1,$2,$3,$4)',[user.id,event.id,'GOING',4])).rows.length,0);
});
await check('Save keeps existing rows, is owner-scoped and version-checked', async () => {
  const plan = await one('select * from amifree_create_preview($1,$2,$3)', [user.id,3,JSON.stringify({...draft,title:'Supper'})]);
  assert.equal((await q('select * from amifree_save($1,$2,$3)',[other.id,plan.id,1])).rows.length,0);
  const saved = await one('select * from amifree_save($1,$2,$3)',[user.id,plan.id,1]);
  assert.equal(saved.status,'SAVED'); assert.equal(saved.version,2);
  assert.equal((await q('select * from amifree_save($1,$2,$3)',[user.id,plan.id,1])).rows.length,0);
  assert.equal((await one("select count(*)::int n from user_actions where action_type='saved'")).n,1);
  await assert.rejects(q("update events set status='BOGUS' where id=$1",[plan.id]));
  assert.equal((await one('select status from events where id=$1',[event.id])).status,'CANCELLED');
  await q('delete from events where id=$1',[plan.id]);
});
await check('retention removes old extraction text and expired pending drafts', async () => {
  await q("insert into event_extractions(user_id,raw_text,extracted_json,validation_state,created_at) values($1,'private','{}','parsed',now()-interval '8 days')",[user.id]);
  await q("update users set pending='{}',pending_expires_at=now()-interval '1 hour' where id=$1",[user.id]);
  await q('select amifree_cleanup(7)');
  assert.equal((await one('select count(*)::int n from event_extractions')).n,0);
  assert.equal((await one('select pending from users where id=$1',[user.id])).pending,null);
});
await check('anonymous/authenticated users cannot read tables or invoke RPCs', async () => {
  for (const role of ['anon','authenticated']) {
    await db.exec(`reset role; set role ${role};`);
    await assert.rejects(q('select * from users'));
    await assert.rejects(q('select * from events'));
    await assert.rejects(q('select * from amifree_ensure_user(999)'));
    await assert.rejects(q('select amifree_cleanup(7)'));
  }
  await db.exec('reset role; set role service_role;');
});
const TABLES = ['users','events','timetable_blocks','event_extractions','user_actions','manual_busy_blocks'];
const SHARED = ['group_events','group_members','event_changes','notification_logs','polls','poll_participants','poll_votes'];
await check('every table has RLS enabled', async () => {
  const result=await q("select relname,relrowsecurity from pg_class where relnamespace='public'::regnamespace and relname=any($1::text[])",[[...TABLES,...SHARED]]);
  assert.equal(result.rows.length,TABLES.length+SHARED.length); assert.ok(result.rows.every(r=>r.relrowsecurity));
});
await check('manual blocks reject malformed rules and survive a timetable reimport', async () => {
  const weekly = "insert into manual_busy_blocks(user_id,title,kind,weekday,start_time,end_time,start_date) values($1,'Work','WEEKLY',2,$2,$3,'2026-10-07')";
  await q(weekly,[user.id,'18:00','22:00']);
  await assert.rejects(q(weekly,[user.id,'22:00','18:00']));
  await assert.rejects(q("insert into manual_busy_blocks(user_id,title,kind) values($1,'Busy','ONCE')",[user.id]));
  await doImport([block]);
  assert.equal((await one('select count(*)::int n from manual_busy_blocks where user_id=$1',[user.id])).n,1);
  await db.exec('reset role; set role anon;');
  await assert.rejects(q('select * from manual_busy_blocks'));
  await assert.rejects(q('select * from amifree_save($1,$2,$3)',[user.id,event.id,1]));
  await db.exec('reset role; set role service_role;');
});
await check('group cards: organiser-only edits propagate to members and record a change', async () => {
  const third = await one('select * from amifree_ensure_user($1)', [1003]);
  const card = await one('select * from amifree_create_group_event($1,$2,$3,$4)',[user.id,-500,10,JSON.stringify(draft)]);
  for (const [who,status] of [[other.id,'GOING'],[third.id,'DECLINED']]) await q('select * from amifree_group_rsvp($1,$2,$3)',[who,card.id,status]);
  await assert.rejects(q('select * from amifree_group_rsvp($1,$2,$3)',[other.id,card.id,'CANCELLED']));
  const moved = {...draft,start_at:'2026-10-09T20:00:00+08:00',end_at:'2026-10-09T22:00:00+08:00'};
  // A member, and a stale version, cannot change the shared card.
  assert.equal((await q('select * from amifree_update_group_event($1,$2,$3,$4,$5)',[other.id,card.id,JSON.stringify(moved),1,false])).rows.length,0);
  assert.equal((await q('select * from amifree_update_group_event($1,$2,$3,$4,$5)',[user.id,card.id,JSON.stringify(moved),9,false])).rows.length,0);
  const updated = await one('select * from amifree_update_group_event($1,$2,$3,$4,$5)',[user.id,card.id,JSON.stringify(moved),1,false]);
  assert.equal(updated.version,2);
  const rows = (await q('select status,start_at from events where group_event_id=$1 order by status',[card.id])).rows;
  assert.deepEqual(rows.map(r=>r.status),['DECLINED','GOING']);
  assert.ok(rows.every(r=>new Date(r.start_at).getTime()===new Date(moved.start_at).getTime()));
  const change = await one('select * from event_changes where group_event_id=$1',[card.id]);
  assert.equal(change.version,2); assert.notEqual(change.before.start_at,change.after.start_at);
  // The change queued one alert for the member who kept the plan: not the decliner or organiser.
  const queued = (await q("select user_id,dedupe_key from notification_logs where kind='CHANGE'")).rows;
  assert.deepEqual(queued,[{user_id:other.id,dedupe_key:`${card.id}:2`}]);
  await one('select * from amifree_update_group_event($1,$2,$3,$4,$5)',[user.id,card.id,'{}',2,true]);
  assert.equal((await one("select count(*)::int n from notification_logs where kind='CHANGE' and user_id=$1",[other.id])).n,2);
  const claimed = (await q('select * from amifree_claim_notifications(10)')).rows;
  assert.equal(claimed.length,2); assert.ok(claimed.every(r=>r.status==='sending' && r.attempts===1));
  assert.equal((await q('select * from amifree_claim_notifications(10)')).rows.length,0);
  await q('select amifree_finish_notification($1,$2,$3)',[claimed[0].id,true,false]);
  await q('select amifree_finish_notification($1,$2,$3)',[claimed[1].id,false,true]);
  assert.deepEqual((await q('select status from notification_logs order by status')).rows.map(r=>r.status),['failed','sent']);
  await assert.rejects(q("insert into notification_logs(user_id,kind,dedupe_key) values($1,'CHANGE',$2)",[other.id,`${card.id}:2`]));
  assert.equal((await one("select count(*)::int n from events where group_event_id=$1 and status='CANCELLED'",[card.id])).n,2);
  assert.equal((await q('select * from amifree_group_rsvp($1,$2,$3)',[other.id,card.id,'GOING'])).rows.length,0);
  await db.exec('reset role; set role anon;');
  await assert.rejects(q('select * from group_events'));
  await assert.rejects(q('select * from amifree_group_counts($1)',[card.id]));
  await assert.rejects(q('select * from notification_logs'));
  await assert.rejects(q('select * from polls'));
  await assert.rejects(q('select * from poll_votes'));
  await assert.rejects(q('select * from amifree_poll_tally($1)',[card.id]));
  await assert.rejects(q('select * from amifree_claim_notifications(1)'));
  await db.exec('reset role; set role service_role;');
  await q('delete from users where id=$1',[third.id]);
});
await check('user deletion cascades to all personal data', async () => {
  await q('delete from users where id=$1',[user.id]);
  for (const table of TABLES.filter(t => t !== 'users')) {
    assert.equal((await one(`select count(*)::int n from ${table} where user_id=$1`,[user.id])).n,0);
  }
  assert.equal((await one('select count(*)::int n from users where id=$1',[other.id])).n,1);
  // Shared cards outlive their organiser; other members keep their linked plans.
  const orphan = await one('select creator_user_id from group_events');
  assert.equal(orphan.creator_user_id,null);
  assert.equal((await one('select count(*)::int n from events where group_event_id is not null')).n,1);
  await q('delete from group_events');
  await q('delete from events');
});
await check('development seed is reproducible and scoped to synthetic users', async () => {
  const seed = await readFile(new URL('../../app/db/seed.sql', import.meta.url), 'utf8');
  await db.exec(seed); await db.exec(seed);
  assert.equal((await one('select count(*)::int n from users where telegram_user_id in (999000001,999000002)')).n,2);
  assert.equal((await one('select count(*)::int n from events')).n,1);
  assert.equal((await one('select count(*)::int n from timetable_blocks')).n,1);
  assert.equal((await one('select count(*)::int n from users where id=$1',[other.id])).n,1);
});
await check('aggregate analytics SQL executes with seeded data', async () => {
  const analytics = await readFile(new URL('../analytics.sql', import.meta.url), 'utf8');
  const reports = await db.exec(analytics);
  assert.equal(reports.length,4);
  assert.equal(Number(reports[0].rows[0].onboarding_percent),100);
  assert.equal(Number(reports[1].rows[0].forwarded_events),1);
  assert.equal(Number(reports[2].rows[0].clashes_detected),1);
  assert.equal(Number(reports[2].rows[0].median_processing_ms),1250);
});
await check('migration 007 moves a single calendar link and its busy times across', async () => {
  const legacy = await one('select * from amifree_ensure_user($1)', [1007]);
  await q("update users set personal_calendar_url='sealed-link', personal_calendar_synced_at=now() where id=$1", [legacy.id]);
  await q("insert into calendar_busy_blocks(user_id,start_at,end_at) values($1,'2026-10-09T19:00:00+08:00','2026-10-09T20:00:00+08:00')", [legacy.id]);
  await db.exec('reset role');
  await db.exec(migrations.find(m => m.includes('Several personal calendar links')));
  await db.exec('set role service_role');
  const cals = (await q('select * from personal_calendars where user_id=$1', [legacy.id])).rows;
  assert.equal(cals.length, 1); assert.equal(cals[0].sealed_url, 'sealed-link');
  const blocks = (await q('select * from calendar_busy_blocks where user_id=$1', [legacy.id])).rows;
  assert.equal(blocks.length, 1); assert.equal(blocks[0].calendar_id, cals[0].id);
  assert.equal((await one('select personal_calendar_url from users where id=$1', [legacy.id])).personal_calendar_url, null);
  // Replacing one calendar leaves another calendar's busy times alone.
  const second = await one("insert into personal_calendars(user_id,sealed_url,url_hash,label) values($1,'s2','h2','Events') returning *", [legacy.id]);
  await q('select amifree_replace_calendar($1,$2,$3)', [second.id, JSON.stringify([{start_at:'2026-10-10T12:00:00+08:00', end_at:'2026-10-10T13:00:00+08:00'}]), '2026-10-09T00:00:00+08:00']);
  await q('select amifree_replace_calendar($1,$2,$3)', [cals[0].id, '[]', '2026-10-09T00:00:00+08:00']);
  const left = (await q('select calendar_id from calendar_busy_blocks where user_id=$1', [legacy.id])).rows;
  assert.deepEqual(left.map(r => r.calendar_id), [second.id]);
  await q('delete from users where id=$1', [legacy.id]);
  assert.equal((await one('select count(*)::int n from personal_calendars where user_id=$1', [legacy.id])).n, 0);
});
await db.close();
console.log(`${count} PostgreSQL schema checks passed.`);
