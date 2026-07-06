import { router } from '../trpc.js';
import { authRouter } from './auth.router.js';
import { candlesRouter } from './candles.router.js';
import { instrumentsRouter } from './instruments.router.js';

export const appRouter = router({
  auth: authRouter,
  instruments: instrumentsRouter,
  candles: candlesRouter
});

export type AppRouter = typeof appRouter;
