import { Collection, Document, MongoClient } from 'mongodb';

import { env } from '../env.js';

let client: MongoClient | null = null;

export const getMongoClient = async (): Promise<MongoClient> => {
  if (!client) {
    client = new MongoClient(env.MONGO_URI);
    await client.connect();
  }

  return client;
};

export const closeMongoClient = async (): Promise<void> => {
  if (client) {
    await client.close();
    client = null;
  }
};

export const collection = async <T extends Document>(name: string): Promise<Collection<T>> => {
  const mongoClient = await getMongoClient();
  return mongoClient.db().collection<T>(name);
};
