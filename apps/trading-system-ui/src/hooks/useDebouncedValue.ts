import { useEffect, useState } from 'react';

/** Delays reflecting `value` until it's stopped changing for `delayMs`. Used for the symbol
 * search box: firing a query on every keystroke is wasteful for any source, and actively breaks
 * the UX for IB specifically -- most partial symbols mid-word (e.g. "N", "NQ") don't resolve to a
 * real contract, and each failed lookup still has to round-trip through IB's own request-pacing
 * queue before the box can show results for what the user actually finished typing. */
export const useDebouncedValue = <T>(value: T, delayMs: number): T => {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);

  return debounced;
};
