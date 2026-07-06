import type { JobsOptions } from 'bullmq';

export enum Queues {
  MARKET_DATA_LIVE = 'market-data-live-queue',
  MARKET_DATA_HISTORICAL = 'market-data-historical-queue',
  MARKET_DATA_GAPS = 'market-data-gaps-queue'
}

// Use function names as job names -- keeps the queue and the worker's switch statement in sync.
export enum QueueJobNames {
  testJob = 'testJob',
  refreshLatestCandles = 'refreshLatestCandles',
  backfillHistoricalCandles = 'backfillHistoricalCandles',
  fillCandleGaps = 'fillCandleGaps'
}

type JobDataMap = {
  [QueueJobNames.testJob]: Record<string, never>;
  [QueueJobNames.refreshLatestCandles]: { instrumentId: string };
  [QueueJobNames.backfillHistoricalCandles]: { instrumentId: string; from: string; to: string };
  [QueueJobNames.fillCandleGaps]: { instrumentId: string };
};

type QueueJob<K extends QueueJobNames> = {
  name: K;
  data: JobDataMap[K];
  opts?: JobsOptions;
};

export type AllQueueJobs = {
  [K in QueueJobNames]: QueueJob<K>;
}[QueueJobNames];
