import { readdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createPool } from './dependencies.js';

const migrationsDir = resolve(__dirname, '../migrations');

async function migrate() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error('DATABASE_URL is required');
  const migrations = (await readdir(migrationsDir))
    .map((file) => ({ file, match: /^(\d{3})_[a-z0-9_]+\.sql$/.exec(file) }))
    .filter((entry) => entry.match)
    .map((entry) => ({ file: entry.file, version: Number(entry.match![1]) }))
    .sort((a, b) => a.version - b.version);

  const pool = createPool(databaseUrl);
  try {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query('SELECT pg_advisory_xact_lock(902039001)');
      await client.query('CREATE TABLE IF NOT EXISTS notification_migrations (version integer PRIMARY KEY)');
      const applied = new Set(
        (await client.query<{ version: number }>('SELECT version FROM notification_migrations')).rows
          .map((row) => row.version),
      );
      for (const { file, version } of migrations) {
        if (applied.has(version)) continue;
        await client.query(await readFile(resolve(migrationsDir, file), 'utf8'));
        await client.query('INSERT INTO notification_migrations (version) VALUES ($1)', [version]);
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
