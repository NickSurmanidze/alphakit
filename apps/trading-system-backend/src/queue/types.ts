import type { JobsOptions } from 'bullmq';

import type { BaseResolution, InstrumentSource } from '../modules/instruments/instruments.types.js';

// One BullMQ queue per (job kind, source) pair -- not one shared queue per job kind -- so a
// source with a hard external rate limit (IB, ~1 call/10s -- see connectors/ib/rateLimiter.ts)
// can never head-of-line-block a faster source (Yahoo, Binance) sitting behind it in the same
// FIFO queue. Before this split, all three sources shared one historical queue: a backlog of a
// few hundred IB backfill jobs made freshly-enqueued Yahoo jobs wait the better part of an hour
// for their turn, even though Yahoo itself was never rate-limited and could have drained
// instantly. Splitting by source lets each queue's worker concurrency reflect that source's real
// throughput instead of the slowest source in the mix.
export enum Queues {
  MARKET_DATA_LIVE_BINANCE = 'market-data-live-binance-queue',
  MARKET_DATA_LIVE_YAHOO = 'market-data-live-yahoo-queue',
  MARKET_DATA_LIVE_IB = 'market-data-live-ib-queue',
  MARKET_DATA_LIVE_DATABENTO = 'market-data-live-databento-queue',
  MARKET_DATA_HISTORICAL_BINANCE = 'market-data-historical-binance-queue',
  MARKET_DATA_HISTORICAL_YAHOO = 'market-data-historical-yahoo-queue',
  MARKET_DATA_HISTORICAL_IB = 'market-data-historical-ib-queue',
  MARKET_DATA_HISTORICAL_DATABENTO = 'market-data-historical-databento-queue',
  MARKET_DATA_GAPS_BINANCE = 'market-data-gaps-binance-queue',
  MARKET_DATA_GAPS_YAHOO = 'market-data-gaps-yahoo-queue',
  MARKET_DATA_GAPS_IB = 'market-data-gaps-ib-queue',
  MARKET_DATA_GAPS_DATABENTO = 'market-data-gaps-databento-queue'
}

const LIVE_QUEUE_BY_SOURCE: Record<InstrumentSource, Queues> = {
  binance: Queues.MARKET_DATA_LIVE_BINANCE,
  yahoo: Queues.MARKET_DATA_LIVE_YAHOO,
  ib: Queues.MARKET_DATA_LIVE_IB,
  databento: Queues.MARKET_DATA_LIVE_DATABENTO
};
const HISTORICAL_QUEUE_BY_SOURCE: Record<InstrumentSource, Queues> = {
  binance: Queues.MARKET_DATA_HISTORICAL_BINANCE,
  yahoo: Queues.MARKET_DATA_HISTORICAL_YAHOO,
  ib: Queues.MARKET_DATA_HISTORICAL_IB,
  databento: Queues.MARKET_DATA_HISTORICAL_DATABENTO
};
const GAPS_QUEUE_BY_SOURCE: Record<InstrumentSource, Queues> = {
  binance: Queues.MARKET_DATA_GAPS_BINANCE,
  yahoo: Queues.MARKET_DATA_GAPS_YAHOO,
  ib: Queues.MARKET_DATA_GAPS_IB,
  databento: Queues.MARKET_DATA_GAPS_DATABENTO
};

export const liveQueueFor = (source: InstrumentSource): Queues => LIVE_QUEUE_BY_SOURCE[source];
export const historicalQueueFor = (source: InstrumentSource): Queues => HISTORICAL_QUEUE_BY_SOURCE[source];
export const gapsQueueFor = (source: InstrumentSource): Queues => GAPS_QUEUE_BY_SOURCE[source];

// Use function names as job names -- keeps the queue and the worker's switch statement in sync.
export enum QueueJobNames {
  testJob = 'testJob',
  refreshLatestCandles = 'refreshLatestCandles',
  backfillHistoricalCandles = 'backfillHistoricalCandles',
  fillCandleGaps = 'fillCandleGaps'
}

type JobDataMap = {
  [QueueJobNames.testJob]: Record<string, never>;
  // One tick for one collected resolution, fired at that resolution's own native period (see
  // cron.ts's per-resolution schedules) -- not one job covering every resolution on a single
  // global cadence. Also cascades into the *open* bucket of every coarser collected resolution;
  // see refreshLatestCandles.ts.
  [QueueJobNames.refreshLatestCandles]: { instrumentId: string; resolution: BaseResolution };
  // Scoped to one resolution: unlike live refresh, historical backfill ranges genuinely differ
  // per collected resolution (e.g. a future's '1_day' backfill covers decades while its
  // '5_minute' backfill covers weeks), so each resolution needs its own job/range.
  [QueueJobNames.backfillHistoricalCandles]: { instrumentId: string; resolution: BaseResolution; from: string; to: string };
  // Loops every collected resolution internally and enqueues per-resolution backfill jobs for
  // whatever gaps it finds -- see fillCandleGaps.ts.
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
