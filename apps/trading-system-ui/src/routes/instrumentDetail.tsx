import { ExternalLink } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';

import type { Candle } from '@/components/instruments/CandlestickChart';
import { CandlestickChart } from '@/components/instruments/CandlestickChart';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useCandleHistory } from '@/hooks/useCandleHistory';
import { Source, SOURCE_LABELS } from '@/lib/instrumentTypes';
import {
  BaseResolution,
  finestResolution,
  Resolution,
  RESOLUTION_LABELS,
  sourceResolutionFor,
  viewableResolutions
} from '@/lib/resolutions';
import { trpc } from '@/lib/trpc';

const scaleCandle = (candle: Candle, multiplier: number): Candle => ({
  ...candle,
  open: candle.open * multiplier,
  high: candle.high * multiplier,
  low: candle.low * multiplier,
  close: candle.close * multiplier
});

const ChartForResolution = ({
  instrumentId,
  resolution,
  collectedResolutions,
  knownEarliestDate,
  priceMultiplier
}: {
  instrumentId: string;
  resolution: Resolution;
  collectedResolutions: BaseResolution[];
  knownEarliestDate: Date | null;
  /** 1 = show the raw quoted price as-is (points); the instrument's pointValue = show dollar
   * notional per point instead. Applied client-side so the same fetched/subscribed data serves
   * either view -- no separate query per mode. */
  priceMultiplier: number;
}) => {
  const { candles, isInitialLoading, isLoadingOlder, loadOlder } = useCandleHistory({
    instrumentId,
    resolution,
    knownEarliestDate
  });
  const [liveCandle, setLiveCandle] = useState<Candle | null>(null);

  // Only a resolution the instrument explicitly collects ever gets a live pub/sub publish (see
  // refreshLatestCandles.ts) -- subscribing at a purely-derived resolution would just sit idle.
  trpc.candles.onLatestCandle.useSubscription(
    { instrumentId, resolution },
    { enabled: collectedResolutions.includes(resolution as BaseResolution), onData: (data: Candle) => setLiveCandle(data) }
  );

  if (isInitialLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  const scaledCandles = priceMultiplier === 1 ? candles : candles.map(c => scaleCandle(c, priceMultiplier));
  const scaledLiveCandle = liveCandle && priceMultiplier !== 1 ? scaleCandle(liveCandle, priceMultiplier) : liveCandle;

  return (
    <div className="flex flex-col gap-2">
      <div className="h-5 text-xs text-muted-foreground">{isLoadingOlder ? 'Loading older data…' : null}</div>
      <CandlestickChart data={scaledCandles} liveCandle={scaledLiveCandle} height={520} onLoadMoreHistory={loadOlder} />
    </div>
  );
};

type ResolutionCoverage = {
  cacheFrom: string | null;
  cacheTo: string | null;
  status: string;
  earliestAvailableDate: string | null;
};

interface DetailInstrument {
  id: string;
  source: string;
  assetClass: string;
  sourceSymbol: string;
  displaySymbol: string;
  description: string | null;
  baseCurrency?: string;
  quoteCurrency?: string;
  calendarVenue: string;
  collectedResolutions: Partial<Record<BaseResolution, ResolutionCoverage>>;
  pointValue: number;
}

const formatDate = (iso: string | null): string => (iso ? new Date(iso).toLocaleDateString() : '—');

/** Metadata header: description, venue, and a per-resolution coverage table -- everything needed
 * to answer "what is this instrument and how much data do we actually have for it" without
 * leaving the chart page. */
const InstrumentInfo = ({ instrument }: { instrument: DetailInstrument }) => {
  const collectedKeys = Object.keys(instrument.collectedResolutions) as BaseResolution[];

  return (
    <div className="flex flex-col gap-3 rounded-md border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-2">
          <h1 className="text-lg font-semibold">{instrument.displaySymbol}</h1>
          {instrument.description ? <span className="text-sm text-muted-foreground">{instrument.description}</span> : null}
          {instrument.source === 'yahoo' ? (
            <a
              href={`https://finance.yahoo.com/quote/${encodeURIComponent(instrument.sourceSymbol)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Yahoo Finance <ExternalLink className="size-3" />
            </a>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          <Badge variant="secondary">{SOURCE_LABELS[instrument.source as Source] ?? instrument.source}</Badge>
          <Badge variant="secondary">{instrument.assetClass}</Badge>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <div className="text-xs text-muted-foreground">Symbol</div>
          <div>{instrument.sourceSymbol}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Venue</div>
          <div>{instrument.calendarVenue}</div>
        </div>
        {instrument.baseCurrency || instrument.quoteCurrency ? (
          <div>
            <div className="text-xs text-muted-foreground">Currency</div>
            <div>{[instrument.baseCurrency, instrument.quoteCurrency].filter(Boolean).join(' / ')}</div>
          </div>
        ) : null}
        {instrument.pointValue !== 1 ? (
          <div>
            <div className="text-xs text-muted-foreground">Point value</div>
            <div>${instrument.pointValue.toLocaleString()} / point</div>
          </div>
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="text-xs text-muted-foreground">Resolutions collected</div>
        <div className="flex flex-col gap-1">
          {collectedKeys.map(resolution => {
            const coverage = instrument.collectedResolutions[resolution]!;
            return (
              <div key={resolution} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="w-16 shrink-0 font-medium">{RESOLUTION_LABELS[resolution]}</span>
                <Badge variant={coverage.status === 'finished' ? 'default' : 'secondary'} className="shrink-0">
                  {coverage.status}
                </Badge>
                <span className="text-muted-foreground">
                  {formatDate(coverage.cacheFrom)} → {formatDate(coverage.cacheTo)}
                </span>
                {coverage.earliestAvailableDate ? (
                  <span className="text-xs text-muted-foreground">
                    (source has data back to {formatDate(coverage.earliestAvailableDate)})
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

const InstrumentChart = ({ instrument }: { instrument: DetailInstrument }) => {
  const collectedKeys = Object.keys(instrument.collectedResolutions) as BaseResolution[];
  const finest = finestResolution(collectedKeys);
  const options = viewableResolutions(collectedKeys);

  // Resolution lives in the URL (?resolution=...) so a refresh keeps whatever the user had
  // selected instead of always resetting to the instrument's finest collected resolution.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedResolution = searchParams.get('resolution') as Resolution | null;
  const [resolution, setResolutionState] = useState<Resolution>(
    requestedResolution && options.includes(requestedResolution) ? requestedResolution : (finest ?? '1_day')
  );

  const setResolution = (value: Resolution) => {
    setResolutionState(value);
    setSearchParams(
      prev => {
        const next = new URLSearchParams(prev);
        next.set('resolution', value);
        return next;
      },
      { replace: true }
    );
  };

  // Points vs. dollar-notional-per-point -- only meaningful (and only shown) for instruments
  // whose raw quote isn't already a dollar price, e.g. index futures quoted in abstract points
  // (see pointValues.ts on the backend). Lives in the URL for the same reason resolution does.
  const requestedPriceMode = searchParams.get('priceMode');
  const [priceMode, setPriceModeState] = useState<'points' | 'usd'>(requestedPriceMode === 'usd' ? 'usd' : 'points');

  const setPriceMode = (value: 'points' | 'usd') => {
    setPriceModeState(value);
    setSearchParams(
      prev => {
        const next = new URLSearchParams(prev);
        next.set('priceMode', value);
        return next;
      },
      { replace: true }
    );
  };

  const priceMultiplier = priceMode === 'usd' ? instrument.pointValue : 1;

  // The earliest date actually known for *this specific viewed resolution* -- not just the
  // instrument's overall finest collected resolution, which can be wrong whenever they differ
  // (e.g. an instrument collecting both '5_minute', with only a couple months of depth, and
  // '1_day', with years -- viewing '1_day' must bound scroll-back against '1_day's own earliest
  // date, not '5_minute's much later one, or scroll-back stops years too soon). See
  // sourceResolutionFor's doc comment for the general rule.
  const sourceResolution = sourceResolutionFor(resolution, collectedKeys);
  const sourceCoverage = sourceResolution ? instrument.collectedResolutions[sourceResolution] : undefined;
  const knownEarliestDateStr = sourceCoverage?.earliestAvailableDate ?? sourceCoverage?.cacheFrom ?? null;
  const knownEarliestDate = knownEarliestDateStr ? new Date(knownEarliestDateStr) : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">Chart</h2>
        <div className="flex items-center gap-2">
          {instrument.pointValue !== 1 ? (
            <div className="flex items-center rounded-md border p-0.5">
              <Button
                type="button"
                size="sm"
                variant={priceMode === 'points' ? 'default' : 'ghost'}
                className="h-7 px-2"
                onClick={() => setPriceMode('points')}
              >
                Points
              </Button>
              <Button
                type="button"
                size="sm"
                variant={priceMode === 'usd' ? 'default' : 'ghost'}
                className="h-7 px-2"
                onClick={() => setPriceMode('usd')}
              >
                USD
              </Button>
            </div>
          ) : null}
          <Select value={resolution} onValueChange={value => setResolution(value as Resolution)}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {options.map(value => (
                <SelectItem key={value} value={value}>
                  {RESOLUTION_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <ChartForResolution
        key={resolution}
        instrumentId={instrument.id}
        resolution={resolution}
        priceMultiplier={priceMultiplier}
        collectedResolutions={collectedKeys}
        knownEarliestDate={knownEarliestDate}
      />
    </div>
  );
};

export const InstrumentDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const instruments = trpc.instruments.list.useQuery();
  const [sourceFilter, setSourceFilter] = useState<Source | 'all'>('all');

  if (instruments.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  const instrument = instruments.data?.find(i => i.id === id);

  if (!instrument) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">Instrument not found.</p>
        <Button variant="outline" size="sm" asChild>
          <Link to="/instruments">Back to instruments</Link>
        </Button>
      </div>
    );
  }

  const options = (instruments.data ?? []).filter(i => sourceFilter === 'all' || i.source === sourceFilter);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={sourceFilter} onValueChange={value => setSourceFilter(value as Source | 'all')}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            <SelectItem value="binance">{SOURCE_LABELS.binance}</SelectItem>
            <SelectItem value="yahoo">{SOURCE_LABELS.yahoo}</SelectItem>
            <SelectItem value="ib">{SOURCE_LABELS.ib}</SelectItem>
          </SelectContent>
        </Select>

        <Select value={instrument.id} onValueChange={value => navigate(`/instruments/${value}`)}>
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map(i => (
              <SelectItem key={i.id} value={i.id}>
                {i.displaySymbol}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <InstrumentInfo instrument={instrument} />

      <InstrumentChart key={instrument.id} instrument={instrument} />
    </div>
  );
};
