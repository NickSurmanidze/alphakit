import type { Job, JobsOptions } from 'bullmq';

import { env } from '../env.js';

// Prefixes queue keys per environment so dev/staging/prod (or feature branches) sharing one
// Redis instance don't process each other's jobs.
export const getQueueEnvPrefix = (): string => `alphakit-${env.NODE_ENV}`;

// Historical backfill jobs previously had no `attempts` configured at all -- a single transient
// failure (a dropped IB connection, a source rate-limit blip) permanently stranded the resolution
// at `status: 'inProgress'` forever, since fillCandleGaps only ever re-scans resolutions already
// at `'finished'`. 3 attempts with backoff gives a real transient failure a chance to clear before
// giving up; a genuinely broken request (bad symbol, unsupported resolution) still fails for good
// after that rather than retrying indefinitely.
export const BACKFILL_JOB_OPTS: JobsOptions = {
  attempts: 3,
  backoff: { type: 'exponential', delay: 5000 }
};

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
