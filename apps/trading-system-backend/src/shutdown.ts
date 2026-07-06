import type { Server } from 'node:http';

import type { Worker } from 'bullmq';
import cron from 'node-cron';

import { closeMongoClient } from './db/mongo.js';
import { closeRedis } from './db/redis.js';
import { closeTimescale } from './db/timescale.js';
import { closeQueues } from './queue/queues.js';

const FORCE_EXIT_MS = 8000;

const closeHttpServer = (server: Server): Promise<void> =>
  new Promise((resolve, reject) => {
    server.close(err => (err ? reject(err) : resolve()));
  });

/**
 * Releases everything that can otherwise keep the event loop alive past tsx/BullMQ/Docker's
 * kill grace period (cron timers, the HTTP server, Redis, Mongo, Timescale connections) --
 * previously only the queue workers were closed on SIGTERM, so nothing else ever released its
 * handles, the process never exited on its own, and the watcher had to SIGKILL it after a
 * timeout. Exported separately from registerGracefulShutdown so it can be unit tested without
 * touching real OS signals or process.exit.
 */
export const runShutdownSequence = async (params: { httpServer: Server; workers: Worker[] }): Promise<void> => {
  const { httpServer, workers } = params;

  await cron.shutdown();
  await Promise.all(workers.map(worker => worker.close()));
  await closeQueues();
  await closeHttpServer(httpServer);
  await Promise.all([closeRedis(), closeMongoClient(), closeTimescale()]);
};

export const registerGracefulShutdown = (params: { httpServer: Server; workers: Worker[] }): void => {
  let shuttingDown = false;

  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.info(`${signal} received, shutting down...`);

    const forceExitTimer = setTimeout(() => {
      console.error(`Graceful shutdown did not finish within ${FORCE_EXIT_MS}ms, forcing exit.`);
      process.exit(1);
    }, FORCE_EXIT_MS);
    forceExitTimer.unref();

    try {
      await runShutdownSequence(params);
      clearTimeout(forceExitTimer);
      process.exit(0);
    } catch (err) {
      console.error('Error during shutdown:', err);
      process.exit(1);
    }
  };

  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));
};
