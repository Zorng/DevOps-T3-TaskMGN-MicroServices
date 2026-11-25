import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createPool } from './dependencies.js';

async function migrate() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error('DATABASE_URL is required');
  const migration = await readFile(resolve(__dirname, '../migrations/001_notifications.sql'), 'utf8');
  const pool = createPool(databaseUrl);
  try {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query('SELECT pg_advisory_xact_lock(902039001)');
      await client.query('CREATE TABLE IF NOT EXISTS notification_migrations (version integer PRIMARY KEY)');
      const result = await client.query('SELECT version FROM notification_migrations WHERE version = 1');
      if (!result.rowCount) {
        await client.query(migration);
        await client.query('INSERT INTO notification_migrations (version) VALUES (1)');
      }
      await client.query('COMMIT');
    } finally {
      client.release(true);
    }
  } finally {
    await pool.end();
  }
}

migrate().catch(() => {
  console.error('Notification migration failed. Check DATABASE_URL and database permissions.');
  process.exitCode = 1;
});
