import cron from 'node-cron';

import { BaseResolution, InstrumentSource } from '../modules/instruments/instruments.types.js';
import { listInstruments, listLiveInstruments } from '../modules/instruments/instruments.repository.js';
import { addQueueJob, queues } from '../queue/queues.js';
import { gapsQueueFor, liveQueueFor, QueueJobNames } from '../queue/types.js';
import { runCodeBlockOnOneServerOnly } from './cron-utils.js';

// Backstop against a slow-to-drain live queue growing without bound -- mirrors
// fillCandleGaps.ts's own historical-queue backlog cap, for the same reason: a source with a hard
// external rate limit (IB, ~1 call/10s -- see connectors/ib/rateLimiter.ts) can be structurally
// unable to keep up with "refresh every live instrument every tick" once there are more than a
// handful of live instruments on it, no matter how efficient the fetch itself is. Without this,
// cron just keeps adding a full batch on top of an already-backed-up queue every single tick
// forever. Checked per source (each source has its own live queue, see queue/types.ts) so a
// backed-up IB queue only ever pauses IB's own refresh, never Binance's or Yahoo's.
const MAX_LIVE_BACKLOG_BEFORE_PAUSING_REFRESH = 60;

// One schedule per collected resolution, each firing at that resolution's own native period --
// not a single "every minute" schedule fanned out to every live instrument regardless of what it
// actually collects. A symbol collecting only daily data has no business generating 1,440
// live-refresh jobs a day; a symbol collecting 5-minute data doesn't need, and can't usefully act
// on, a tick every single minute. See refreshLatestCandles.ts for what a tick actually does --
// besides refreshing its own resolution, it also cascades into the *open* bucket of every coarser
// collected resolution, so e.g. the 5-minute tick alone keeps an instrument's hourly/daily charts
// visibly live between their own (less frequent, but real-fetch/finalizing) ticks.
const LIVE_TICK_SCHEDULES: { resolution: BaseResolution; cronExpr: string; unlockInSeconds: number }[] = [
  { resolution: '1_minute', cronExpr: '*/1 * * * *', unlockInSeconds: 55 },
  { resolution: '5_minute', cronExpr: '*/5 * * * *', unlockInSeconds: 4 * 60 + 55 },
  { resolution: '1_hour', cronExpr: '0 * * * *', unlockInSeconds: 59 * 60 + 55 },
  { resolution: '1_day', cronExpr: '0 0 * * *', unlockInSeconds: 23 * 60 * 60 + 55 * 60 }
];

export const startListeningToCronEvents = (): void => {
  for (const { resolution, cronExpr, unlockInSeconds } of LIVE_TICK_SCHEDULES) {
    cron.schedule(cronExpr, async () => {
      await runCodeBlockOnOneServerOnly({
        name: `refresh-latest-candles-${resolution}`,
        unlockInSeconds,
        fn: async () => {
          const instruments = (await listLiveInstruments()).filter(instrument => resolution in instrument.collectedResolutions);

          // One backlog check per source per tick, not per instrument -- cached here since every
          // instrument for a given source shares the same queue and thus the same answer.
          const overBacklogBySource = new Map<InstrumentSource, boolean>();

          for (const instrument of instruments) {
            const queueName = liveQueueFor(instrument.source);

            let overBacklog = overBacklogBySource.get(instrument.source);
            if (overBacklog === undefined) {
              const { waiting, active } = await queues[queueName].getJobCounts('waiting', 'active');
              overBacklog = waiting + active >= MAX_LIVE_BACKLOG_BEFORE_PAUSING_REFRESH;
              overBacklogBySource.set(instrument.source, overBacklog);
            }
            if (overBacklog) {
              continue;
            }

            await addQueueJob({
              queueName,
              job: {
                name: QueueJobNames.refreshLatestCandles,
                data: { instrumentId: instrument._id.toHexString(), resolution }
              }
            });
          }
        }
      });
    });
  }

  // Fan out one fillCandleGaps job per instrument every 5 minutes, onto that instrument's own
  // source-specific gaps queue.
  cron.schedule('*/5 * * * *', async () => {
    await runCodeBlockOnOneServerOnly({
      name: 'fill-candle-gaps',
      unlockInSeconds: 4 * 60 + 55,
      fn: async () => {
        const instruments = await listInstruments();
        for (const instrument of instruments) {
          await addQueueJob({
            queueName: gapsQueueFor(instrument.source),
            job: {
              name: QueueJobNames.fillCandleGaps,
              data: { instrumentId: instrument._id.toHexString() }
            }
          });
        }
      }
    });
  });

  console.info('Cron scheduler started.');
};
