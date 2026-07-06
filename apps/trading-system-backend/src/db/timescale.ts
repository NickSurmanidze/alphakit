import pg from 'pg';

import { env } from '../env.js';

let pool: pg.Pool | null = null;

export const timescale = (): pg.Pool => {
  if (!pool) {
    pool = new pg.Pool({ connectionString: env.TIMESCALE_URL });
  }

  return pool;
};

export const closeTimescale = async (): Promise<void> => {
  if (pool) {
    await pool.end();
    pool = null;
  }
};
