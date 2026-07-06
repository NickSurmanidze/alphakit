// Yahoo's chart/search endpoints are unofficial with no documented rate limit -- a single
// misconfigured instrument (e.g. gap-checking decades of history) can otherwise burst enough
// concurrent requests to get 429'd repeatedly. This serializes every Yahoo call through one
// in-process queue with a minimum spacing, independent of how many BullMQ workers or
// instruments are trying to hit Yahoo at once. Process-local only -- fine for a single backend
// instance; would need a Redis-backed limiter to hold across multiple replicas.
const MIN_INTERVAL_MS = 500;

let queue: Promise<void> = Promise.resolve();

export const yahooRateLimited = <T>(fn: () => Promise<T>): Promise<T> => {
  const result = queue.then(fn);
  queue = result.then(
    () => new Promise<void>(resolve => setTimeout(resolve, MIN_INTERVAL_MS)),
    () => new Promise<void>(resolve => setTimeout(resolve, MIN_INTERVAL_MS))
  );
  return result;
};
