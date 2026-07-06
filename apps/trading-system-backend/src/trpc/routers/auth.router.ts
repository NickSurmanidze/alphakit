import { TRPCError } from '@trpc/server';
import type { Response } from 'express';
import { z } from 'zod';

import { isProduction, env } from '../../env.js';
import * as authService from '../../modules/auth/auth.service.js';
import { REFRESH_TOKEN_COOKIE_NAME, REFRESH_TOKEN_MAX_AGE_MS } from '../../modules/auth/jwt.js';
import { protectedProcedure, publicProcedure, router } from '../trpc.js';

const setRefreshTokenCookie = (res: Response, token: string) => {
  res.cookie(REFRESH_TOKEN_COOKIE_NAME, token, {
    httpOnly: true,
    secure: isProduction,
    sameSite: 'lax',
    domain: env.COOKIE_DOMAIN || undefined,
    maxAge: REFRESH_TOKEN_MAX_AGE_MS
  });
};

const clearRefreshTokenCookie = (res: Response) => {
  res.clearCookie(REFRESH_TOKEN_COOKIE_NAME, {
    httpOnly: true,
    secure: isProduction,
    sameSite: 'lax',
    domain: env.COOKIE_DOMAIN || undefined
  });
};

export const authRouter = router({
  login: publicProcedure
    .input(z.object({ email: z.string().email(), password: z.string().min(1) }))
    .mutation(async ({ input, ctx }) => {
      const tokens = await authService.login(input);
      setRefreshTokenCookie(ctx.res, tokens.refreshToken);
      return { accessToken: tokens.accessToken, user: tokens.user };
    }),

  refresh: publicProcedure.mutation(async ({ ctx }) => {
    const refreshToken = ctx.req.cookies?.[REFRESH_TOKEN_COOKIE_NAME];
    if (!refreshToken) {
      throw new TRPCError({ code: 'UNAUTHORIZED', message: 'No refresh token' });
    }

    const tokens = await authService.refreshSession(refreshToken);
    setRefreshTokenCookie(ctx.res, tokens.refreshToken);
    return { accessToken: tokens.accessToken, user: tokens.user };
  }),

  logout: protectedProcedure.mutation(async ({ ctx }) => {
    await authService.logout(ctx.user.id);
    clearRefreshTokenCookie(ctx.res);
    return { success: true };
  }),

  me: protectedProcedure.query(({ ctx }) => ctx.user)
});
