import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

const db = new PGlite();
const schema = await readFile(new URL('../../app/db/schema.sql', import.meta.url), 'utf8');
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
await db.exec(schema);
await db.exec(schema);
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
await check('all five tables have RLS enabled', async () => {
  const result=await q("select relname,relrowsecurity from pg_class where relname=any($1::text[])",[['users','events','timetable_blocks','event_extractions','user_actions']]);
  assert.equal(result.rows.length,5); assert.ok(result.rows.every(r=>r.relrowsecurity));
});
await check('user deletion cascades to all personal data', async () => {
  await q('delete from users where id=$1',[user.id]);
  for (const table of ['events','timetable_blocks','event_extractions','user_actions']) {
    assert.equal((await one(`select count(*)::int n from ${table} where user_id=$1`,[user.id])).n,0);
  }
  assert.equal((await one('select count(*)::int n from users where id=$1',[other.id])).n,1);
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
await db.close();
console.log(`${count} PostgreSQL schema checks passed.`);
