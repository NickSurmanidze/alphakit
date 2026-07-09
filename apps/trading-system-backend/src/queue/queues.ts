import { Queue } from 'bullmq';

import { redis } from '../db/redis.js';
import { getQueueEnvPrefix } from './queue-utils.js';
import { AllQueueJobs, Queues } from './types.js';

const defaultQueueOptions = {
  prefix: getQueueEnvPrefix(),
  connection: redis()
};

export const queues: Record<Queues, Queue> = Object.fromEntries(
  Object.values(Queues).map(name => [name, new Queue(name, defaultQueueOptions)])
) as Record<Queues, Queue>;

export const addQueueJob = async ({ queueName, job }: { queueName: Queues; job: AllQueueJobs }) => {
  return queues[queueName].add(job.name, job.data, job.opts);
};

export const closeQueues = async (): Promise<void> => {
  await Promise.all(Object.values(queues).map(queue => queue.close()));
};
