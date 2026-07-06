import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpError, withRetry } from './withRetry.js';

describe('withRetry', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns the result immediately on success without retrying', async () => {
    const fn = vi.fn().mockResolvedValue('ok');
    await expect(withRetry(fn)).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('retries on a 429 and eventually succeeds', async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new HttpError('rate limited', 429))
      .mockResolvedValueOnce('ok');

    const promise = withRetry(fn, { baseDelayMs: 10 });
    await vi.runAllTimersAsync();

    await expect(promise).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('retries on a 5xx but not on a 4xx other than 429', async () => {
    const fn = vi.fn().mockRejectedValue(new HttpError('bad request', 400));

    const promise = withRetry(fn, { baseDelayMs: 10 });
    await expect(promise).rejects.toThrow('bad request');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('stops after the configured number of retries', async () => {
    const fn = vi.fn().mockRejectedValue(new HttpError('server error', 503));

    const promise = withRetry(fn, { retries: 3, baseDelayMs: 10 });
    promise.catch(() => {});
    await vi.runAllTimersAsync();

    await expect(promise).rejects.toThrow('server error');
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('retries on a network-level TypeError (e.g. fetch DNS/connection failure)', async () => {
    const fn = vi.fn().mockRejectedValueOnce(new TypeError('fetch failed')).mockResolvedValueOnce('ok');

    const promise = withRetry(fn, { baseDelayMs: 10 });
    await vi.runAllTimersAsync();

    await expect(promise).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
