import zlib from 'node:zlib';

import { Redis } from 'ioredis';

import { env } from '../env.js';

let client: Redis | null = null;

// BullMQ requires maxRetriesPerRequest: null on any connection it's handed.
export const redis = (): Redis => {
  if (!client) {
    client = new Redis({
      host: env.REDIS_HOST,
      port: env.REDIS_PORT,
      ...(env.REDIS_PASSWORD ? { password: env.REDIS_PASSWORD } : {}),
      maxRetriesPerRequest: null
    });
  }

  return client;
};

export const closeRedis = async (): Promise<void> => {
  await Promise.all([
    (async () => {
      if (client) {
        await client.quit();
        client = null;
      }
    })(),
    (async () => {
      // Separate connection from `client` (see subscribeToChannel below) -- closing only the
      // main client left this one open, which alone is enough to keep the event loop alive and
      // block a clean process exit on shutdown.
      if (subscriberClient) {
        await subscriberClient.quit();
        subscriberClient = null;
      }
    })()
  ]);
};

const deflate = (value: string): string => zlib.deflateSync(Buffer.from(value)).toString('base64');
const inflate = (value: string): string => zlib.inflateSync(Buffer.from(value, 'base64')).toString();

export const getCachedObject = async <T>({
  id,
  ttl = 60,
  onCacheMiss,
  compress = false
}: {
  id: string;
  ttl?: number;
  onCacheMiss?: (id: string) => Promise<T> | T;
  compress?: boolean;
}): Promise<T | undefined> => {
  const value = await redis().get(id);

  if (!value) {
    if (!onCacheMiss) {
      return undefined;
    }

    const newValue = await onCacheMiss(id);
    if (newValue !== undefined && newValue !== null) {
      const toStore = compress ? deflate(JSON.stringify(newValue)) : JSON.stringify(newValue);
      await redis().setex(id, ttl, toStore);
    }
    return newValue;
  }

  return compress ? JSON.parse(inflate(value)) : JSON.parse(value);
};

export const setCachedObject = async <T>({
  id,
  ttl = 60,
  value,
  compress = false
}: {
  id: string;
  ttl?: number;
  value: T;
  compress?: boolean;
}): Promise<void> => {
  const toStore = compress ? deflate(JSON.stringify(value)) : JSON.stringify(value);
  await redis().setex(id, ttl, toStore);
};

export const publishRedis = async ({
  channel,
  message
}: {
  channel: string;
  message: string;
}): Promise<void> => {
  try {
    await redis().publish(channel, message);
  } catch {
    // publishing must never take down the caller (e.g. a candle-refresh job)
  }
};

// Uses a dedicated connection: once a client calls `.subscribe`, ioredis puts it into
// subscriber mode and it can no longer issue normal commands.
let subscriberClient: Redis | null = null;

export const subscribeToChannel = (
  channel: string,
  onMessage: (message: string) => void
): (() => Promise<void>) => {
  if (!subscriberClient) {
    subscriberClient = new Redis({
      host: env.REDIS_HOST,
      port: env.REDIS_PORT,
      ...(env.REDIS_PASSWORD ? { password: env.REDIS_PASSWORD } : {}),
      maxRetriesPerRequest: null
    });
  }

  const client_ = subscriberClient;
  const listener = (triggeredChannel: string, message: string) => {
    if (triggeredChannel === channel) {
      onMessage(message);
    }
  };

  client_.subscribe(channel);
  client_.on('message', listener);

  return async () => {
    client_.off('message', listener);
    await client_.unsubscribe(channel);
  };
};
