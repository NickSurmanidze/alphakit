import type { CreateExpressContextOptions } from '@trpc/server/adapters/express';

import { getUserFromRefreshToken } from '../modules/auth/auth.service.js';
import { REFRESH_TOKEN_COOKIE_NAME, verifyAccessToken } from '../modules/auth/jwt.js';
import { getUserById } from '../modules/users/users.repository.js';
import { PublicUser, toPublicUser } from '../modules/users/users.types.js';

export const createContext = async ({ req, res }: CreateExpressContextOptions) => {
  let user: PublicUser | null = null;

  const authHeader = req.headers.authorization;
  if (authHeader?.startsWith('Bearer ')) {
    const token = authHeader.slice('Bearer '.length);
    try {
      const payload = verifyAccessToken(token);
      const dbUser = await getUserById(payload.userId);
      if (dbUser && dbUser.tokenVersion === payload.tokenVersion) {
        user = toPublicUser(dbUser);
      }
    } catch {
      // invalid/expired access token -> fall through to the refresh-cookie check below
    }
  }

  // Fallback for requests that can't carry a custom Authorization header at all -- namely the
  // SSE subscription link, which uses the browser's native EventSource under the hood. The
  // httpOnly refresh cookie (sent automatically with withCredentials) is the only usable
  // credential there, same as the Bull Board auth gate.
  if (!user) {
    const refreshToken = req.cookies?.[REFRESH_TOKEN_COOKIE_NAME];
    if (refreshToken) {
      const dbUser = await getUserFromRefreshToken(refreshToken);
      if (dbUser) {
        user = toPublicUser(dbUser);
      }
    }
  }

  return { req, res, user };
};

export type Context = Awaited<ReturnType<typeof createContext>>;
