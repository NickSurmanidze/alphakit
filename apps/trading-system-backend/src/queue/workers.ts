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

// Returns the created workers so the caller can close them as part of one coordinated shutdown
// sequence (see shutdown.ts) -- closing them here via our own SIGTERM handler raced with, and
// duplicated, the shutdown logic index.ts also needs to run for the HTTP server and DB clients.
export const startQueueWorkers = (): Worker[] => {
  const workers: Worker[] = [
    new Worker(Queues.MARKET_DATA_LIVE, job => processJob(job), {
      ...defaultWorkerOptions,
      concurrency: 10
    }),
    new Worker(Queues.MARKET_DATA_HISTORICAL, job => processJob(job), {
      ...defaultWorkerOptions,
      concurrency: 2
    }),
    new Worker(Queues.MARKET_DATA_GAPS, job => processJob(job), {
      ...defaultWorkerOptions,
      concurrency: 2
    })
  ];

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
