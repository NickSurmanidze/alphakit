import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getCachedObject = vi.fn();
const setCachedObject = vi.fn();

vi.mock('../db/redis.js', () => ({ getCachedObject, setCachedObject }));

const { runCodeBlockOnOneServerOnly } = await import('./cron-utils.js');

// runCodeBlockOnOneServerOnly always sleeps a random 0-1000ms jitter before checking the lock;
// fake timers avoid every test actually waiting up to a second.
const runAndFlush = <T>(promise: Promise<T>): Promise<T> => {
  const flushed = vi.runAllTimersAsync().then(() => promise);
  return flushed;
};

describe('runCodeBlockOnOneServerOnly', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getCachedObject.mockReset();
    setCachedObject.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('acquires the lock and runs the block when no other replica holds it', async () => {
    getCachedObject.mockResolvedValue(undefined);
    const fn = vi.fn().mockResolvedValue(undefined);

    await runAndFlush(runCodeBlockOnOneServerOnly({ name: 'test-job', unlockInSeconds: 30, fn }));

    expect(setCachedObject).toHaveBeenCalledWith({ id: 'cron-lock:test-job', ttl: 30, value: true });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('skips the block entirely when another replica already holds the lock', async () => {
    getCachedObject.mockResolvedValue(true);
    const fn = vi.fn();

    await runAndFlush(runCodeBlockOnOneServerOnly({ name: 'test-job', fn }));

    expect(setCachedObject).not.toHaveBeenCalled();
    expect(fn).not.toHaveBeenCalled();
  });

  it('swallows an error thrown by the block instead of letting it propagate', async () => {
    getCachedObject.mockResolvedValue(undefined);
    const fn = vi.fn().mockRejectedValue(new Error('boom'));

    await expect(runAndFlush(runCodeBlockOnOneServerOnly({ name: 'test-job', fn }))).resolves.toBeUndefined();
  });
});
