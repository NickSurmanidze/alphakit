import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createExpressMiddleware } from '@trpc/server/adapters/express';
import cookieParser from 'cookie-parser';
import cors from 'cors';
import express from 'express';

import { apiDocsHandler } from './admin/apiDocs.js';
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

const API_DOCS_BASE_PATH = '/docs/api';

// Dev-only: a plain readable view of the tRPC router's input schemas (generated fresh from the
// running router via zod's own toJSONSchema() -- no third-party introspection package involved,
// see AGENTS.md for why that matters). No business being reachable in a deployed environment.
if (!isProduction) {
  app.get(API_DOCS_BASE_PATH, requireValidRefreshCookie, apiDocsHandler);
}

if (isProduction) {
  const uiDist = path.resolve(__dirname, '../../trading-system-ui/dist');
  app.use(express.static(uiDist));
  // Express 5 (path-to-regexp v8) requires a named wildcard instead of a bare '*'
  app.get('/*splat', (_req, res) => {
    res.sendFile(path.join(uiDist, 'index.html'));
  });
}

const printStartupBanner = () => {
  const base = `http://localhost:${env.PORT}`;
  const lines = [
    ['🚀', 'API', `${base} (${env.NODE_ENV})`],
    ['🔌', 'tRPC endpoint', `${base}/trpc`],
    ['❤️ ', 'Health check', `${base}/health`],
    ['📋', 'Bull Board', `${base}${BULL_BOARD_BASE_PATH}`]
  ];
  if (!isProduction) {
    lines.push(['📖', 'API schema', `${base}${API_DOCS_BASE_PATH}`]);
    lines.push(['🖥️ ', 'Frontend (dev)', env.UI_DEV_ORIGIN]);
  }

  const labelWidth = Math.max(...lines.map(([, label]) => label.length));
  console.log('');
  for (const [emoji, label, url] of lines) {
    console.log(`  ${emoji} ${label.padEnd(labelWidth)}  ${url}`);
  }
  console.log('');
};

const httpServer = app.listen(env.PORT, () => {
  printStartupBanner();
});

const workers = startQueueWorkers();
startListeningToCronEvents();

registerGracefulShutdown({ httpServer, workers });
