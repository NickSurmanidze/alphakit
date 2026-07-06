import cron from 'node-cron';

import { listInstruments, listLiveInstruments } from '../modules/instruments/instruments.repository.js';
import { addQueueJob } from '../queue/queues.js';
import { QueueJobNames, Queues } from '../queue/types.js';
import { runCodeBlockOnOneServerOnly } from './cron-utils.js';

export const startListeningToCronEvents = (): void => {
  // Fan out one refreshLatestCandles job per live-tracked instrument every minute.
  cron.schedule('*/1 * * * *', async () => {
    await runCodeBlockOnOneServerOnly({
      name: 'refresh-latest-candles',
      unlockInSeconds: 55,
      fn: async () => {
        const instruments = await listLiveInstruments();
        for (const instrument of instruments) {
          await addQueueJob({
            queueName: Queues.MARKET_DATA_LIVE,
            job: {
              name: QueueJobNames.refreshLatestCandles,
              data: { instrumentId: instrument._id.toHexString() }
            }
          });
        }
      }
    });
  });

  // Fan out one fillCandleGaps job per instrument every 5 minutes.
  cron.schedule('*/5 * * * *', async () => {
    await runCodeBlockOnOneServerOnly({
      name: 'fill-candle-gaps',
      unlockInSeconds: 4 * 60 + 55,
      fn: async () => {
        const instruments = await listInstruments();
        for (const instrument of instruments) {
          await addQueueJob({
            queueName: Queues.MARKET_DATA_GAPS,
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
