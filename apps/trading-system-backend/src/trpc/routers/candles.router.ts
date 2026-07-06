import { z } from 'zod';

import { subscribeToChannel } from '../../db/redis.js';
import { getCandles } from '../../modules/candles/candleStore.js';
import { protectedProcedure, router } from '../trpc.js';

const resolutionSchema = z.enum(['1_minute', '15_minute', '1_hour', '1_day']);

export const candlesRouter = router({
  getCandles: protectedProcedure
    .input(
      z.object({
        instrumentId: z.string(),
        resolution: resolutionSchema,
        from: z.string(),
        to: z.string(),
        limit: z.number().int().positive().max(20_000).optional()
      })
    )
    .query(({ input }) =>
      getCandles({
        instrumentId: input.instrumentId,
        resolution: input.resolution,
        from: new Date(input.from),
        to: new Date(input.to),
        limit: input.limit
      })
    ),

  // Pushes the latest candle for one instrument/resolution while a chart is mounted, over SSE --
  // no WebSocket involved. Bridged off the Redis pub/sub channel refreshLatestCandles publishes
  // to on every 1-minute cron tick.
  onLatestCandle: protectedProcedure
    .input(z.object({ instrumentId: z.string(), resolution: resolutionSchema }))
    .subscription(async function* ({ input, signal }) {
      const queue: string[] = [];
      let wake: (() => void) | null = null;

      const unsubscribe = subscribeToChannel(`candles:${input.instrumentId}:${input.resolution}`, message => {
        queue.push(message);
        wake?.();
      });

      signal?.addEventListener('abort', () => wake?.());

      try {
        while (!signal?.aborted) {
          if (queue.length === 0) {
            await new Promise<void>(resolve => {
              wake = resolve;
            });
          }
          while (queue.length > 0) {
            const message = queue.shift();
            if (message) {
              yield JSON.parse(message);
            }
          }
        }
      } finally {
        await unsubscribe();
      }
    })
});
