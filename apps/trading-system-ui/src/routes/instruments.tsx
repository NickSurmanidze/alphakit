import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { AssetClass, Source } from '@/lib/instrumentTypes';
import { BaseResolution, RESOLUTION_LABELS } from '@/lib/resolutions';
import { trpc } from '@/lib/trpc';

const BASE_RESOLUTIONS: BaseResolution[] = ['1_minute', '1_hour', '1_day'];

// Calendar depends on what actually trades, not which source it came from -- a Yahoo future
// (e.g. NQ=F) trades ~23h/day on CME Globex, nothing like NYSE's 9:30-16:00 session. Getting
// this wrong doesn't just miscount coverage: gap-detection re-flags the "missing" off-NYSE-hours
// data as gaps forever, since it can never be filled.
const getCalendarVenue = (source: Source, assetClass: AssetClass): string => {
  if (source === 'binance') {
    return 'CRYPTO_24_7';
  }
  return assetClass === 'future' || assetClass === 'forex' ? 'FUTURES_NEAR_CONTINUOUS' : 'NYSE';
};

const AddInstrumentForm = ({ onRegistered }: { onRegistered: () => void }) => {
  const [source, setSource] = useState<Source>('binance');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<{
    symbol: string;
    displaySymbol: string;
    assetClass: string;
    baseCurrency?: string;
    quoteCurrency?: string;
  } | null>(null);
  // Min resolution to actually fetch -- everything coarser (e.g. daily) is derived from this.
  // Defaults to 1 hour: cheaper to store/query than 1-minute, while the live refresh job still
  // keeps the current hour's bar updating every minute rather than only on the hour.
  const [resolution, setResolution] = useState<BaseResolution>('1_hour');
  const [backfillFullHistory, setBackfillFullHistory] = useState(false);

  const search = trpc.instruments.searchSymbols.useQuery(
    { source, query },
    { enabled: query.length > 0 }
  );

  const register = trpc.instruments.register.useMutation({
    onSuccess: () => {
      setSelected(null);
      setQuery('');
      onRegistered();
    }
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={source} onValueChange={value => setSource(value as Source)}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="binance">Binance</SelectItem>
            <SelectItem value="yahoo">Yahoo Finance</SelectItem>
          </SelectContent>
        </Select>

        <Input
          className="flex-1"
          placeholder="Search symbol (e.g. BTCUSDT, AAPL)"
          value={query}
          onChange={e => {
            setQuery(e.target.value);
            setSelected(null);
          }}
        />

        <Select value={resolution} onValueChange={value => setResolution(value as BaseResolution)}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {BASE_RESOLUTIONS.map(value => (
              <SelectItem key={value} value={value}>
                {RESOLUTION_LABELS[value]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center gap-2">
        <Switch id="backfill-full-history" checked={backfillFullHistory} onCheckedChange={setBackfillFullHistory} />
        <Label htmlFor="backfill-full-history" className="text-sm font-normal text-muted-foreground">
          Backfill full history since inception (instead of the last 30 days)
        </Label>
      </div>

      {query && search.data ? (
        <div className="flex max-h-48 flex-col gap-1 overflow-y-auto">
          {search.data.map(result => (
            <button
              key={result.symbol}
              type="button"
              onClick={() => setSelected(result)}
              className={`rounded-md px-2 py-1 text-left text-sm hover:bg-muted ${
                selected?.symbol === result.symbol ? 'bg-muted' : ''
              }`}
            >
              {result.displaySymbol} <span className="text-muted-foreground">({result.symbol})</span>
            </button>
          ))}
        </div>
      ) : null}

      <Button
        disabled={!selected || register.isPending}
        onClick={() =>
          selected &&
          register.mutate({
            source,
            sourceSymbol: selected.symbol,
            displaySymbol: selected.displaySymbol,
            assetClass: selected.assetClass as AssetClass,
            baseCurrency: selected.baseCurrency,
            quoteCurrency: selected.quoteCurrency,
            baseResolution: resolution,
            calendarVenue: getCalendarVenue(source, selected.assetClass as AssetClass),
            cacheHistoricalPrices: true,
            cacheLivePrices: true,
            backfillFullHistory
          })
        }
      >
        {register.isPending ? 'Registering…' : 'Register instrument'}
      </Button>
      {register.error ? <p className="text-sm text-destructive">{register.error.message}</p> : null}
    </div>
  );
};

const AddInstrumentDialog = ({ onRegistered }: { onRegistered: () => void }) => {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">+ Add instrument</Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add instrument</DialogTitle>
        </DialogHeader>
        <AddInstrumentForm
          onRegistered={() => {
            onRegistered();
            setOpen(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
};

export const InstrumentsPage = () => {
  const utils = trpc.useUtils();
  const navigate = useNavigate();
  const instruments = trpc.instruments.list.useQuery();
  const updateFlags = trpc.instruments.updateFlags.useMutation({
    onSuccess: () => utils.instruments.list.invalidate()
  });
  const updateResolution = trpc.instruments.updateResolution.useMutation({
    onSuccess: () => utils.instruments.list.invalidate()
  });
  const refresh = trpc.instruments.refresh.useMutation();
  const deleteInstrument = trpc.instruments.delete.useMutation({
    onSuccess: () => utils.instruments.list.invalidate()
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Instruments</h1>
        <AddInstrumentDialog onRegistered={() => utils.instruments.list.invalidate()} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Resolution</TableHead>
            <TableHead>Earliest available</TableHead>
            <TableHead>Coverage</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Live</TableHead>
            <TableHead>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(instruments.data ?? []).map(instrument => (
            <TableRow
              key={instrument.id}
              className="cursor-pointer"
              onClick={() => navigate(`/instruments/${instrument.id}`)}
            >
              <TableCell>{instrument.displaySymbol}</TableCell>
              <TableCell>{instrument.source}</TableCell>
              <TableCell onClick={e => e.stopPropagation()}>
                <Select
                  value={instrument.baseResolution}
                  disabled={updateResolution.isPending && updateResolution.variables?.id === instrument.id}
                  onValueChange={value => {
                    if (value === instrument.baseResolution) return;
                    if (
                      window.confirm(
                        `Change resolution to ${RESOLUTION_LABELS[value as BaseResolution]}? This deletes all cached candle data for ${instrument.displaySymbol} and re-downloads it at the new resolution.`
                      )
                    ) {
                      updateResolution.mutate({ id: instrument.id, baseResolution: value as BaseResolution });
                    }
                  }}
                >
                  <SelectTrigger className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BASE_RESOLUTIONS.map(value => (
                      <SelectItem key={value} value={value}>
                        {RESOLUTION_LABELS[value]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {instrument.earliestAvailableDates[instrument.baseResolution]
                  ? new Date(
                      instrument.earliestAvailableDates[instrument.baseResolution] as string
                    ).toLocaleDateString()
                  : '—'}
              </TableCell>
              <TableCell>
                {instrument.cacheFrom ? new Date(instrument.cacheFrom).toLocaleDateString() : '—'} →{' '}
                {instrument.cacheTo ? new Date(instrument.cacheTo).toLocaleString() : '—'}
              </TableCell>
              <TableCell>
                <Badge variant={instrument.status === 'finished' ? 'default' : 'secondary'}>
                  {instrument.status}
                </Badge>
              </TableCell>
              <TableCell onClick={e => e.stopPropagation()}>
                <Switch
                  checked={instrument.cacheLivePrices}
                  onCheckedChange={checked =>
                    updateFlags.mutate({ id: instrument.id, cacheLivePrices: checked })
                  }
                />
              </TableCell>
              <TableCell onClick={e => e.stopPropagation()}>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={refresh.isPending && refresh.variables?.id === instrument.id}
                    onClick={() => refresh.mutate({ id: instrument.id })}
                  >
                    Refresh
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deleteInstrument.isPending && deleteInstrument.variables?.id === instrument.id}
                    onClick={() => {
                      if (
                        window.confirm(
                          `Delete ${instrument.displaySymbol}? This permanently removes the instrument and all of its cached candle data.`
                        )
                      ) {
                        deleteInstrument.mutate({ id: instrument.id });
                      }
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
};
