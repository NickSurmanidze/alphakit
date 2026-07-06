import { ObjectId } from 'mongodb';

import { collection } from '../../db/mongo.js';
import { UserDoc } from './users.types.js';

const users = () => collection<UserDoc>('users');

export const getUserByEmail = async (email: string): Promise<UserDoc | null> => {
  return (await users()).findOne({ email });
};

export const getUserById = async (id: string): Promise<UserDoc | null> => {
  return (await users()).findOne({ _id: new ObjectId(id) });
};

export const createUser = async ({
  email,
  passwordHash
}: {
  email: string;
  passwordHash: string;
}): Promise<UserDoc> => {
  const existing = await getUserByEmail(email);
  if (existing) {
    throw new Error(`User with email ${email} already exists`);
  }

  const doc: UserDoc = {
    _id: new ObjectId(),
    email,
    passwordHash,
    tokenVersion: 0,
    createdAt: new Date()
  };

  await (await users()).insertOne(doc);
  return doc;
};

export const bumpTokenVersion = async (id: string): Promise<void> => {
  await (await users()).updateOne({ _id: new ObjectId(id) }, { $inc: { tokenVersion: 1 } });
};
