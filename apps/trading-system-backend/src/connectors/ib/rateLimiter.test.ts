import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Fresh module state per test -- ibRateLimited's queue arrays are module-level, so a task left
// over from one test would otherwise bleed into the next.
const importFresh = async () => {
  vi.resetModules();
  return import('./rateLimiter.js');
};

describe('ibRateLimited', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs tasks in FIFO order within the same priority', async () => {
    const { ibRateLimited } = await importFresh();
    const order: number[] = [];

    const p1 = ibRateLimited(async () => {
      order.push(1);
    });
    const p2 = ibRateLimited(async () => {
      order.push(2);
    });

    await vi.runAllTimersAsync();
    await Promise.all([p1, p2]);

    expect(order).toEqual([1, 2]);
  });

  it('runs a high-priority task before already-queued low-priority tasks, without preempting one already in flight', async () => {
    const { ibRateLimited } = await importFresh();
    const order: string[] = [];

    // Regression test: a bulk historical backfill (low priority) that's already queued up
    // hundreds of calls used to starve an interactive search (high priority) for many minutes --
    // see rateLimiter.ts's module comment. High-priority work must jump the remaining low-priority
    // queue, but the first low-priority call (already in flight by the time 'high' arrives) still
    // has to finish first -- there's no cancelling an in-progress IB request.
    const inFlight = ibRateLimited(async () => {
      order.push('low-1 (in flight)');
    }); // 'low' by default
    const queuedLow = ibRateLimited(async () => {
      order.push('low-2 (queued)');
    });
    const queuedHigh = ibRateLimited(async () => {
      order.push('high (search)');
    }, 'high');

    await vi.runAllTimersAsync();
    await Promise.all([inFlight, queuedLow, queuedHigh]);

    expect(order).toEqual(['low-1 (in flight)', 'high (search)', 'low-2 (queued)']);
  });

  it('spaces consecutive dispatches by the minimum interval', async () => {
    const { ibRateLimited } = await importFresh();
    const timestamps: number[] = [];

    const p1 = ibRateLimited(async () => {
      timestamps.push(Date.now());
    });
    const p2 = ibRateLimited(async () => {
      timestamps.push(Date.now());
    });

    await vi.runAllTimersAsync();
    await Promise.all([p1, p2]);

    expect(timestamps[1] - timestamps[0]).toBeGreaterThanOrEqual(10_500);
  });
});
