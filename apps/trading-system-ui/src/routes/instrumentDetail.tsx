import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import type { Candle } from '@/components/instruments/CandlestickChart';
import { CandlestickChart } from '@/components/instruments/CandlestickChart';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useCandleHistory } from '@/hooks/useCandleHistory';
import { Source, SOURCE_LABELS } from '@/lib/instrumentTypes';
import { BaseResolution, Resolution, RESOLUTION_LABELS, viewableResolutions } from '@/lib/resolutions';
import { trpc } from '@/lib/trpc';

const ChartForResolution = ({
  instrumentId,
  resolution,
  baseResolution,
  knownEarliestDate
}: {
  instrumentId: string;
  resolution: Resolution;
  baseResolution: BaseResolution;
  knownEarliestDate: Date | null;
}) => {
  const { candles, isInitialLoading, isLoadingOlder, loadOlder } = useCandleHistory({
    instrumentId,
    resolution,
    knownEarliestDate
  });
  const [liveCandle, setLiveCandle] = useState<Candle | null>(null);

  // Only the instrument's own base resolution ever gets a live pub/sub publish (see
  // refreshLatestCandles.ts) -- subscribing at any other viewed resolution would just sit idle.
  trpc.candles.onLatestCandle.useSubscription(
    { instrumentId, resolution },
    { enabled: resolution === baseResolution, onData: (data: Candle) => setLiveCandle(data) }
  );

  if (isInitialLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="h-5 text-xs text-muted-foreground">{isLoadingOlder ? 'Loading older data…' : null}</div>
      <CandlestickChart data={candles} liveCandle={liveCandle} height={520} onLoadMoreHistory={loadOlder} />
    </div>
  );
};

const InstrumentChart = ({
  instrument
}: {
  instrument: {
    id: string;
    baseResolution: BaseResolution;
    displaySymbol: string;
    cacheFrom: string | null;
    earliestAvailableDates: Partial<Record<BaseResolution, string>>;
  };
}) => {
  const [resolution, setResolution] = useState<Resolution>(instrument.baseResolution);
  const options = viewableResolutions(instrument.baseResolution);

  // The earliest date actually known for this instrument, used so scroll-back loading knows
  // when to stop instead of guessing from how many rows a fetch happened to return (see
  // useCandleHistory). earliestAvailableDates is keyed by base resolution, but the earliest
  // *derived* bar at any coarser viewed resolution can't be any earlier than that anyway, so it
  // doubles as a reasonable bound regardless of which resolution is currently being viewed.
  const knownEarliestDateStr = instrument.earliestAvailableDates[instrument.baseResolution] ?? instrument.cacheFrom;
  const knownEarliestDate = knownEarliestDateStr ? new Date(knownEarliestDateStr) : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{instrument.displaySymbol}</h2>
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

      <ChartForResolution
        key={resolution}
        instrumentId={instrument.id}
        resolution={resolution}
        baseResolution={instrument.baseResolution}
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

        <Badge variant={instrument.status === 'finished' ? 'default' : 'secondary'}>{instrument.status}</Badge>
      </div>

      <InstrumentChart key={instrument.id} instrument={instrument} />
    </div>
  );
};
