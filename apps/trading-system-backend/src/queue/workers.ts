import { Job, Worker } from 'bullmq';

import { redis } from '../db/redis.js';
import { backfillHistoricalCandles } from '../jobs/backfillHistoricalCandles.js';
import { fillCandleGaps } from '../jobs/fillCandleGaps.js';
import { refreshLatestCandles } from '../jobs/refreshLatestCandles.js';
import { getQueueEnvPrefix, handleJobError } from './queue-utils.js';
import { QueueJobNames, Queues } from './types.js';

const defaultWorkerOptions = {
  prefix: getQueueEnvPrefix(),
  connection: redis(),
  removeOnComplete: { age: 7 * 24 * 3600, count: 1000 },
  removeOnFail: { age: 30 * 24 * 3600 }
};

// IB's own in-process rate limiter (connectors/ib/rateLimiter.ts) serializes every call to
// ~1/10s regardless of concurrency here -- concurrency above a handful buys nothing but extra
// BullMQ locks held on jobs that are just parked waiting their turn in that shared queue.
// Binance/Yahoo have no such shared bottleneck (Yahoo's own limiter allows ~2/s), so their
// workers can run considerably more jobs in parallel.
const IB_LOCK_DURATION_MS = 120_000;

interface WorkerSpec {
  queue: Queues;
  concurrency: number;
  lockDuration?: number;
}

const WORKER_SPECS: WorkerSpec[] = [
  { queue: Queues.MARKET_DATA_LIVE_BINANCE, concurrency: 10 },
  { queue: Queues.MARKET_DATA_LIVE_YAHOO, concurrency: 10 },
  { queue: Queues.MARKET_DATA_LIVE_IB, concurrency: 5, lockDuration: IB_LOCK_DURATION_MS },
  { queue: Queues.MARKET_DATA_HISTORICAL_BINANCE, concurrency: 10 },
  { queue: Queues.MARKET_DATA_HISTORICAL_YAHOO, concurrency: 10 },
  { queue: Queues.MARKET_DATA_HISTORICAL_IB, concurrency: 3, lockDuration: IB_LOCK_DURATION_MS },
  { queue: Queues.MARKET_DATA_GAPS_BINANCE, concurrency: 2 },
  { queue: Queues.MARKET_DATA_GAPS_YAHOO, concurrency: 2 },
  { queue: Queues.MARKET_DATA_GAPS_IB, concurrency: 2 }
];

// Returns the created workers so the caller can close them as part of one coordinated shutdown
// sequence (see shutdown.ts) -- closing them here via our own SIGTERM handler raced with, and
// duplicated, the shutdown logic index.ts also needs to run for the HTTP server and DB clients.
export const startQueueWorkers = (): Worker[] => {
  const workers = WORKER_SPECS.map(
    ({ queue, concurrency, lockDuration }) =>
      new Worker(queue, job => processJob(job), {
        ...defaultWorkerOptions,
        concurrency,
        ...(lockDuration ? { lockDuration } : {})
      })
  );

  for (const worker of workers) {
    worker.on('failed', handleJobError);
  }

  console.info('Queue workers started.');
  return workers;
};

export const processJob = async (job: Job): Promise<unknown> => {
  const jobName = job.name as QueueJobNames;

  switch (jobName) {
    case QueueJobNames.testJob:
      return { ok: true };

    case QueueJobNames.refreshLatestCandles:
      return refreshLatestCandles(job.data);

    case QueueJobNames.backfillHistoricalCandles:
      return backfillHistoricalCandles(job.data);

    case QueueJobNames.fillCandleGaps:
      return fillCandleGaps(job.data);

    default:
      return ((exhaustiveCheck: never) => {
        throw new Error(`No handler defined for job name ${exhaustiveCheck}`);
      })(jobName);
  }
};
