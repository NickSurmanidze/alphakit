import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createExpressMiddleware } from '@trpc/server/adapters/express';
import cookieParser from 'cookie-parser';
import cors from 'cors';
import express from 'express';

import { BULL_BOARD_BASE_PATH, createBullBoardRouter } from './admin/bullBoard.js';
import { requireValidRefreshCookie } from './admin/requireValidRefreshCookie.js';
import { startListeningToCronEvents } from './cron/cron.js';
import { startQueueWorkers } from './queue/workers.js';
import { registerGracefulShutdown } from './shutdown.js';
import { createContext } from './trpc/context.js';
import { appRouter } from './trpc/routers/_app.js';
import { env, isProduction } from './env.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();

app.use(cookieParser());

if (!isProduction) {
  app.use(cors({ origin: env.UI_DEV_ORIGIN, credentials: true }));
}

app.get('/health', (_req, res) => {
  res.json({ status: 'ok' });
});

app.use('/trpc', createExpressMiddleware({ router: appRouter, createContext }));

app.use(BULL_BOARD_BASE_PATH, requireValidRefreshCookie, createBullBoardRouter());

if (isProduction) {
  const uiDist = path.resolve(__dirname, '../../trading-system-ui/dist');
  app.use(express.static(uiDist));
  // Express 5 (path-to-regexp v8) requires a named wildcard instead of a bare '*'
  app.get('/*splat', (_req, res) => {
    res.sendFile(path.join(uiDist, 'index.html'));
  });
}

const httpServer = app.listen(env.PORT, () => {
  console.log(`trading-system-backend listening on port ${env.PORT} (${env.NODE_ENV})`);
});

const workers = startQueueWorkers();
startListeningToCronEvents();

registerGracefulShutdown({ httpServer, workers });
