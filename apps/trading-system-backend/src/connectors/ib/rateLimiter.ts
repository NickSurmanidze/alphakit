// IB's historical-data pacing rules are stricter than a generic per-second cap (IBApiNext's own
// `maxReqPerSec` throttles the raw socket, not this specific budget): no identical request within
// 15s, no more than 6 requests for the same contract/tick-type within 2s, and no more than 60
// historical-data requests in any rolling 10-minute window. This serializes every historical-data
// call (contract lookups, head-timestamp lookups, and bar requests alike) through one in-process
// queue with a conservative minimum spacing, independent of how many BullMQ workers are hitting
// IB at once. Process-local only -- fine for a single backend instance; would need a Redis-backed
// limiter to hold across multiple replicas.
//
// 60-per-10-min is the binding constraint under any kind of sustained load (a bulk multi-symbol
// registration, a large backfill) -- that's 1 request per 10s on average, not 2s. An earlier
// version of this file set MIN_INTERVAL_MS to 2000 with a comment claiming that was "more
// conservative" than the 60/10min ceiling, which was backwards: 2s spacing allows ~300
// requests/10min, 5x over the real limit. Harmless for a handful of one-off calls, but a real risk
// of tripping IB's pacing violation (and possible account-level throttling) the moment something
// issues dozens of calls back-to-back -- e.g. registering several futures at once, each needing a
// head-timestamp lookup per resolution. 10_500ms keeps a small margin under the exact 10_000ms/req
// ceiling rather than riding it.
const MIN_INTERVAL_MS = 10_500;

// Two priority tiers, not one FIFO queue: a bulk historical backfill can legitimately enqueue
// hundreds of calls (e.g. a multi-resolution, multi-year IB future), and without this a user
// sitting on the "Add instrument" search box would queue behind all of them -- at 2s/call, a
// couple hundred queued backfill calls is 10+ minutes before a live search even starts, which
// just looks like the search box is broken. High-priority calls (searchSymbols,
// getEarliestAvailableDate -- both always directly user-initiated) always jump ahead of whatever
// bulk work (fetchHistoricalCandles, fetchLatestCandle) is still queued, though never ahead of a
// call already in flight.
interface QueuedTask {
  run: () => Promise<void>;
}

const highQueue: QueuedTask[] = [];
const lowQueue: QueuedTask[] = [];
let draining = false;

const sleep = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms));

const drain = async (): Promise<void> => {
  if (draining) return;
  draining = true;
  try {
    let task: QueuedTask | undefined;
    while ((task = highQueue.shift() ?? lowQueue.shift())) {
      await task.run();
      await sleep(MIN_INTERVAL_MS);
    }
  } finally {
    draining = false;
  }
};

export const ibRateLimited = <T>(fn: () => Promise<T>, priority: 'high' | 'low' = 'low'): Promise<T> => {
  return new Promise<T>((resolve, reject) => {
    const task: QueuedTask = {
      run: () => fn().then(resolve, reject)
    };
    (priority === 'high' ? highQueue : lowQueue).push(task);
    void drain();
  });
};
