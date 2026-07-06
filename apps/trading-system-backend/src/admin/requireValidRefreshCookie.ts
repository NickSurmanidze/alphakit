import type { NextFunction, Request, Response } from 'express';

import { isValidRefreshToken } from '../modules/auth/auth.service.js';
import { REFRESH_TOKEN_COOKIE_NAME } from '../modules/auth/jwt.js';

/** Gates plain-HTTP admin routes (Bull Board) using the same httpOnly refresh cookie set by
 * auth.login -- unlike the in-memory Bearer access token, cookies are sent automatically on a
 * direct new-tab navigation, so this needs no separate token-passing mechanism. */
export const requireValidRefreshCookie = async (req: Request, res: Response, next: NextFunction) => {
  const refreshToken = req.cookies?.[REFRESH_TOKEN_COOKIE_NAME];

  if (refreshToken && (await isValidRefreshToken(refreshToken))) {
    next();
    return;
  }

  res.status(401).send('Unauthorized');
};
