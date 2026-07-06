import { getCachedObject, setCachedObject } from '../db/redis.js';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const randomBetween = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min;

// Lets multiple backend replicas share one cron schedule without double-running a tick:
// each replica waits a random jitter, then only proceeds if it wins a short Redis lock.
// Not a true distributed lock (check-then-set isn't atomic) -- acceptable here since the
// jitter makes a real collision rare and a duplicate run is idempotent (upserts), not harmful.
export const runCodeBlockOnOneServerOnly = async ({
  name,
  unlockInSeconds = 60,
  fn
}: {
  name: string;
  unlockInSeconds?: number;
  fn: () => Promise<void> | void;
}): Promise<void> => {
  await sleep(randomBetween(0, 1000));

  const lockKey = `cron-lock:${name}`;
  const locked = await getCachedObject<boolean>({ id: lockKey });

  if (!locked) {
    await setCachedObject({ id: lockKey, ttl: unlockInSeconds, value: true });
    try {
      await fn();
    } catch (e) {
      console.error(`cron :: ${name} failed`, e);
    }
  }
};
