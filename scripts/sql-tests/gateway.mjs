// Test-only SQL gateway over stdin/stdout. No listening socket or production fallback.
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { PGlite } from '@electric-sql/pglite';

const db = new PGlite();
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
await db.exec(await readFile(new URL('../../app/db/schema.sql', import.meta.url), 'utf8'));
await db.exec('set role service_role;');
console.log(JSON.stringify({ ready: true }));
for await (const line of createInterface({ input: process.stdin })) {
  try {
    const request = JSON.parse(line);
    const result = await db.query(request.sql, request.values || []);
    console.log(JSON.stringify({ rows: result.rows }));
  } catch (error) {
    console.log(JSON.stringify({ error: error.message, code: error.code }));
  }
}
await db.close();
