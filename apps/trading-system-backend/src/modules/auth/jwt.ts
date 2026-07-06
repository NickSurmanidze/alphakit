import jwt from 'jsonwebtoken';

import { env } from '../../env.js';

const ACCESS_TOKEN_EXPIRY = '15m';
const REFRESH_TOKEN_EXPIRY = '30d';

export interface TokenPayload {
  userId: string;
  tokenVersion: number;
}

export const signAccessToken = (payload: TokenPayload): string =>
  jwt.sign(payload, env.ACCESS_TOKEN_SECRET, { expiresIn: ACCESS_TOKEN_EXPIRY });

export const signRefreshToken = (payload: TokenPayload): string =>
  jwt.sign(payload, env.REFRESH_TOKEN_SECRET, { expiresIn: REFRESH_TOKEN_EXPIRY });

export const verifyAccessToken = (token: string): TokenPayload =>
  jwt.verify(token, env.ACCESS_TOKEN_SECRET) as TokenPayload;

export const verifyRefreshToken = (token: string): TokenPayload =>
  jwt.verify(token, env.REFRESH_TOKEN_SECRET) as TokenPayload;

export const REFRESH_TOKEN_COOKIE_NAME = 'refreshToken';
export const REFRESH_TOKEN_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
