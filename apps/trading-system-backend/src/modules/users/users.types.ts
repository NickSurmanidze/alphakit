import { ObjectId } from 'mongodb';

export interface UserDoc {
  _id: ObjectId;
  email: string;
  passwordHash: string;
  tokenVersion: number;
  createdAt: Date;
}

export interface PublicUser {
  id: string;
  email: string;
}

export const toPublicUser = (user: UserDoc): PublicUser => ({
  id: user._id.toHexString(),
  email: user.email
});
