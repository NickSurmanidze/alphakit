import 'dotenv/config';

import { z } from 'zod';

const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  PORT: z.coerce.number().int().positive().default(4000),
  MONGO_URI: z.string().min(1, 'MONGO_URI is required'),
  ACCESS_TOKEN_SECRET: z.string().min(1, 'ACCESS_TOKEN_SECRET is required'),
  REFRESH_TOKEN_SECRET: z.string().min(1, 'REFRESH_TOKEN_SECRET is required'),
  UI_DEV_ORIGIN: z.string().default('http://localhost:5173'),
  COOKIE_DOMAIN: z.string().optional(),

  REDIS_HOST: z.string().default('localhost'),
  REDIS_PORT: z.coerce.number().int().positive().default(63794),
  REDIS_PASSWORD: z.string().optional(),

  TIMESCALE_URL: z.string().min(1, 'TIMESCALE_URL is required'),

  // IB Gateway socket connection -- not a simple API key, this is the TWS Gateway's own login.
  // The gateway container (see infra/docker-compose.yml) does the actual IBKR authentication;
  // the backend just connects to its API socket. Port 4004, NOT 4002: the gateway container's own
  // IBGateway process only ever listens on 127.0.0.1:4002 inside the container -- 4004 is the
  // image's own `socat` relay, which is what's actually published to the host (see the ports
  // comment in infra/docker-compose.yml). Has a default so the app still boots without an IB
  // account configured -- the ib connector fails loudly on first use instead.
  IB_GATEWAY_HOST: z.string().default('localhost'),
  IB_GATEWAY_PORT: z.coerce.number().int().positive().default(4004),
  IB_CLIENT_ID: z.coerce.number().int().nonnegative().default(11)
});

export const env = envSchema.parse(process.env);

export const isProduction = env.NODE_ENV === 'production';
