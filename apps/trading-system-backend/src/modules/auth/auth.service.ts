import { TRPCError } from '@trpc/server';

import { getUserByEmail, getUserById, bumpTokenVersion } from '../users/users.repository.js';
import { PublicUser, toPublicUser } from '../users/users.types.js';
import { verifyPassword } from './password.js';
import { signAccessToken, signRefreshToken, verifyRefreshToken } from './jwt.js';

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  user: PublicUser;
}

const invalidCredentials = () =>
  new TRPCError({ code: 'UNAUTHORIZED', message: 'Invalid email or password' });

const invalidSession = () =>
  new TRPCError({ code: 'UNAUTHORIZED', message: 'Session is no longer valid' });

export const login = async ({
  email,
  password
}: {
  email: string;
  password: string;
}): Promise<AuthTokens> => {
  const user = await getUserByEmail(email);
  if (!user) {
    throw invalidCredentials();
  }

  const valid = await verifyPassword(password, user.passwordHash);
  if (!valid) {
    throw invalidCredentials();
  }

  const payload = { userId: user._id.toHexString(), tokenVersion: user.tokenVersion };

  return {
    accessToken: signAccessToken(payload),
    refreshToken: signRefreshToken(payload),
    user: toPublicUser(user)
  };
};

export const refreshSession = async (refreshToken: string): Promise<AuthTokens> => {
  let payload;
  try {
    payload = verifyRefreshToken(refreshToken);
  } catch {
    throw invalidSession();
  }

  const user = await getUserById(payload.userId);
  if (!user || user.tokenVersion !== payload.tokenVersion) {
    throw invalidSession();
  }

  const newPayload = { userId: user._id.toHexString(), tokenVersion: user.tokenVersion };

  return {
    accessToken: signAccessToken(newPayload),
    refreshToken: signRefreshToken(newPayload),
    user: toPublicUser(user)
  };
};

export const logout = async (userId: string): Promise<void> => {
  await bumpTokenVersion(userId);
};
