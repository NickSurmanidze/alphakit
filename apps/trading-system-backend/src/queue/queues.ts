import { Queue } from 'bullmq';

import { redis } from '../db/redis.js';
import { getQueueEnvPrefix } from './queue-utils.js';
import { AllQueueJobs, Queues } from './types.js';

const defaultQueueOptions = {
  prefix: getQueueEnvPrefix(),
  connection: redis()
};

export const queues: Record<Queues, Queue> = {
  [Queues.MARKET_DATA_LIVE]: new Queue(Queues.MARKET_DATA_LIVE, defaultQueueOptions),
  [Queues.MARKET_DATA_HISTORICAL]: new Queue(Queues.MARKET_DATA_HISTORICAL, defaultQueueOptions),
  [Queues.MARKET_DATA_GAPS]: new Queue(Queues.MARKET_DATA_GAPS, defaultQueueOptions)
};

export const addQueueJob = async ({ queueName, job }: { queueName: Queues; job: AllQueueJobs }) => {
  return queues[queueName].add(job.name, job.data, job.opts);
};

export const closeQueues = async (): Promise<void> => {
  await Promise.all(Object.values(queues).map(queue => queue.close()));
};
