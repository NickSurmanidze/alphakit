import type { Job } from 'bullmq';

import { env } from '../env.js';

// Prefixes queue keys per environment so dev/staging/prod (or feature branches) sharing one
// Redis instance don't process each other's jobs.
export const getQueueEnvPrefix = (): string => `alphakit-${env.NODE_ENV}`;

export const handleJobError = (job?: Job): void => {
  const attempts = job?.opts.attempts ?? 1;
  const attemptsMade = job?.attemptsMade ?? 0;

  if (attempts <= attemptsMade) {
    console.error(
      `Job ${job?.id} (${job?.name}) failed permanently: ${job?.failedReason}`
    );
  } else {
    console.warn(
      `Job ${job?.id} (${job?.name}) failed, will retry (${attemptsMade}/${attempts}): ${job?.failedReason}`
    );
  }
};
