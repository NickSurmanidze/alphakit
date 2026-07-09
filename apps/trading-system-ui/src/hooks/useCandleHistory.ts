import { useCallback, useEffect, useRef, useState } from 'react';

import type { Candle } from '@/components/instruments/CandlestickChart';
import { Resolution, RESOLUTION_MS } from '@/lib/resolutions';
import { trpc } from '@/lib/trpc';

// How many bars' worth of *time* to request per fetch, sized so the initial view shows a
// sensible amount of history at each resolution (a few hours of 1-minute bars, a couple years of
// daily) while keeping every chunk -- including scroll-back loads -- a similarly cheap query.
// This is a time window, not a promise of that many rows: a non-24/7 market (e.g. NYSE, ~252
// trading days/year) returns fewer rows per calendar-day window than a 24/7 one like crypto.
const CHUNK_BARS: Record<Resolution, number> = {
  '1_minute': 360,
  '5_minute': 420,
  '15_minute': 480,
  '1_hour': 336,
  '1_day': 730
};

/**
 * Loads candle history for one instrument/resolution and supports loading further back in time
 * on demand (see CandlestickChart's onLoadMoreHistory) instead of fetching a fixed window up
 * front -- e.g. an instrument with years of backfilled data would otherwise only ever show the
 * most recent chunk.
 *
 * `knownEarliestDate` (typically the instrument's earliestAvailableDates) is the authoritative
 * signal for "is there more to load". Two things this guards against:
 *  - Counting rows per fetch doesn't work for markets that aren't open 24/7 (NYSE trades ~252
 *    days/year, so a 730-*day* window is nowhere near 730 daily bars) -- comparing dates against
 *    a known bound instead of comparing row counts against the requested chunk size fixes that.
 *  - An empty fetch doesn't necessarily mean "no more history" -- it can just be a gap (a long
 *    weekend/holiday stretch, or a real outage) narrower than the known remaining range. Empty
 *    batches are skipped over (the window slides further back and tries again) as long as we
 *    haven't yet reached knownEarliestDate; only once we have, or knownEarliestDate isn't known
 *    at all and a fetch comes back empty, do we conclude there's nothing further back.
 *
 * Does NOT defend against `instrumentId`/`resolution` changing across renders of the same hook
 * instance -- callers must remount (via a `key` on the component that calls this hook, e.g.
 * `key={resolution}`) when either changes, same as CandlePanel's old liveCandle-reset did before
 * being replaced by a key remount. That keeps this hook's own state resets out of an effect body
 * (calling setState synchronously there causes a same-tick re-render for no benefit here, since a
 * fresh mount already starts from empty state).
 */
export const useCandleHistory = (params: {
  instrumentId: string;
  resolution: Resolution;
  knownEarliestDate?: Date | null;
}) => {
  const { instrumentId, resolution, knownEarliestDate } = params;
  const utils = trpc.useUtils();

  const [candles, setCandles] = useState<Candle[]>([]);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const earliestLoadedRef = useRef<Date | null>(null);
  const loadingOlderRef = useRef(false);
  const hasMoreRef = useRef(true);
  // Bumped on every fetchChunk call; a resolution only applies its result if it's still the most
  // recent call in flight. Needed because React 19's Strict Mode runs effects twice in dev --
  // the initial-load effect below fires, gets cleaned up, and fires again immediately, issuing
  // two near-identical fetches for the same window. Without this, both would apply via
  // setCandles(prev => [...result, ...prev]), producing duplicated/out-of-order rows that crash
  // lightweight-charts' setData ("data must be asc ordered by time").
  const latestRequestRef = useRef(0);

  // Fetches [from, to), prepends whatever it finds (if anything) and works out whether it's
  // worth trying an even earlier window next time. `from`/`to` are always exactly one chunk
  // apart, so an empty result still tells us precisely how far the search has covered.
  const fetchChunk = useCallback(
    (to: Date): Promise<void> => {
      const requestId = ++latestRequestRef.current;
      const chunkBars = CHUNK_BARS[resolution];
      const from = new Date(to.getTime() - chunkBars * RESOLUTION_MS[resolution]);

      return utils.candles.getCandles
        .fetch({ instrumentId, resolution, from: from.toISOString(), to: to.toISOString(), limit: chunkBars })
        .then(result => {
          if (latestRequestRef.current !== requestId) return; // superseded by a newer request

          const earliestLoaded = result.length > 0 ? new Date(result[0].timeOpen) : from;
          const reachedKnownStart = knownEarliestDate ? earliestLoaded <= knownEarliestDate : false;
          const more = reachedKnownStart ? false : result.length > 0 || Boolean(knownEarliestDate);

          earliestLoadedRef.current = earliestLoaded;
          hasMoreRef.current = more;
          setHasMore(more);
          if (result.length > 0) {
            setCandles(prev => [...result, ...prev]);
          }
        });
    },
    // utils.candles.getCandles is stable for the lifetime of the TRPCProvider.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [instrumentId, resolution, knownEarliestDate]
  );

  useEffect(() => {
    // isInitialLoading already starts `true` from useState -- this effect only ever runs once
    // per mount (callers remount via `key` on instrumentId/resolution change, see doc comment
    // above), so there's never a stale `false` here that would need resetting back to `true`.
    fetchChunk(new Date())
      .catch(() => undefined)
      .finally(() => setIsInitialLoading(false));
  }, [fetchChunk]);

  const loadOlder = useCallback(() => {
    if (loadingOlderRef.current || !hasMoreRef.current || !earliestLoadedRef.current) {
      return;
    }
    loadingOlderRef.current = true;
    setIsLoadingOlder(true);

    fetchChunk(earliestLoadedRef.current).finally(() => {
      loadingOlderRef.current = false;
      setIsLoadingOlder(false);
    });
  }, [fetchChunk]);

  return { candles, isInitialLoading, isLoadingOlder, hasMore, loadOlder };
};
