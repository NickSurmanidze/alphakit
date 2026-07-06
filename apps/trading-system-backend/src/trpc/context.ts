import type { CreateExpressContextOptions } from '@trpc/server/adapters/express';

import { verifyAccessToken } from '../modules/auth/jwt.js';
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
      // invalid/expired access token, or user no longer matches -> treat as unauthenticated
    }
  }

  return { req, res, user };
};

export type Context = Awaited<ReturnType<typeof createContext>>;
