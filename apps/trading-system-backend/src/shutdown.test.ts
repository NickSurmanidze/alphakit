import type { Server } from 'node:http';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const cronShutdown = vi.fn();
const closeMongoClient = vi.fn();
const closeRedis = vi.fn();
const closeTimescale = vi.fn();
const closeQueues = vi.fn();

vi.mock('node-cron', () => ({ default: { shutdown: cronShutdown } }));
vi.mock('./db/mongo.js', () => ({ closeMongoClient }));
vi.mock('./db/redis.js', () => ({ closeRedis }));
vi.mock('./db/timescale.js', () => ({ closeTimescale }));
vi.mock('./queue/queues.js', () => ({ closeQueues }));

const { runShutdownSequence, registerGracefulShutdown } = await import('./shutdown.js');

const makeHttpServer = (): Server => {
  const close = vi.fn((cb: (err?: Error) => void) => cb());
  return { close } as unknown as Server;
};

describe('runShutdownSequence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('closes cron, workers, queues, the HTTP server, then every DB connection', async () => {
    const callOrder: string[] = [];
    cronShutdown.mockImplementation(async () => callOrder.push('cron'));
    closeQueues.mockImplementation(async () => callOrder.push('queues'));
    closeRedis.mockImplementation(async () => callOrder.push('redis'));
    closeMongoClient.mockImplementation(async () => callOrder.push('mongo'));
    closeTimescale.mockImplementation(async () => callOrder.push('timescale'));

    const worker = { close: vi.fn(async () => callOrder.push('worker')) };
    const httpServer = makeHttpServer();
    (httpServer.close as ReturnType<typeof vi.fn>).mockImplementation((cb: (err?: Error) => void) => {
      callOrder.push('http');
      cb();
    });

    await runShutdownSequence({ httpServer, workers: [worker as never] });

    expect(callOrder.indexOf('cron')).toBeLessThan(callOrder.indexOf('worker'));
    expect(callOrder.indexOf('worker')).toBeLessThan(callOrder.indexOf('queues'));
    expect(callOrder.indexOf('queues')).toBeLessThan(callOrder.indexOf('http'));
    expect(callOrder.indexOf('http')).toBeLessThan(callOrder.indexOf('redis'));
    expect(callOrder.indexOf('http')).toBeLessThan(callOrder.indexOf('mongo'));
    expect(callOrder.indexOf('http')).toBeLessThan(callOrder.indexOf('timescale'));
  });

  it('closes every worker even if there are several', async () => {
    const workerA = { close: vi.fn().mockResolvedValue(undefined) };
    const workerB = { close: vi.fn().mockResolvedValue(undefined) };

    await runShutdownSequence({ httpServer: makeHttpServer(), workers: [workerA as never, workerB as never] });

    expect(workerA.close).toHaveBeenCalledTimes(1);
    expect(workerB.close).toHaveBeenCalledTimes(1);
  });

  it('propagates an error from the HTTP server failing to close', async () => {
    const httpServer = makeHttpServer();
    (httpServer.close as ReturnType<typeof vi.fn>).mockImplementation((cb: (err?: Error) => void) =>
      cb(new Error('already closed'))
    );

    await expect(runShutdownSequence({ httpServer, workers: [] })).rejects.toThrow('already closed');
  });
});

describe('registerGracefulShutdown', () => {
  it('registers exactly one SIGTERM and one SIGINT handler', () => {
    const before = { sigterm: process.listenerCount('SIGTERM'), sigint: process.listenerCount('SIGINT') };

    registerGracefulShutdown({ httpServer: makeHttpServer(), workers: [] });

    expect(process.listenerCount('SIGTERM')).toBe(before.sigterm + 1);
    expect(process.listenerCount('SIGINT')).toBe(before.sigint + 1);
  });
});
