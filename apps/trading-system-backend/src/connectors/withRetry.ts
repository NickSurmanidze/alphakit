export class HttpError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

const isRetryable = (e: unknown): boolean => {
  if (e instanceof HttpError) {
    return e.status === 429 || (e.status >= 500 && e.status < 600);
  }
  // fetch() throws a plain TypeError for network-level failures (DNS, connection reset, etc.)
  return e instanceof TypeError;
};

/** Generic retry/backoff for connector HTTP calls -- source-agnostic, unlike legacy's
 * CCXT-error-name-keyed wrapCcxtCall. Retries on 429/5xx/network errors only. */
export const withRetry = async <T>(
  fn: () => Promise<T>,
  opts?: { retries?: number; baseDelayMs?: number }
): Promise<T> => {
  const retries = opts?.retries ?? 5;
  const baseDelayMs = opts?.baseDelayMs ?? 500;

  let attempt = 0;
  for (;;) {
    try {
      return await fn();
    } catch (e) {
      attempt++;
      if (!isRetryable(e) || attempt >= retries) {
        throw e;
      }
      const delay = baseDelayMs * 2 ** (attempt - 1) + Math.random() * 200;
      await sleep(delay);
    }
  }
};

export const fetchJson = async <T>(url: string, init?: RequestInit): Promise<T> => {
  return withRetry(async () => {
    const res = await fetch(url, init);
    if (!res.ok) {
      throw new HttpError(`${init?.method ?? 'GET'} ${url} -> ${res.status}`, res.status);
    }
    return (await res.json()) as T;
  });
};
