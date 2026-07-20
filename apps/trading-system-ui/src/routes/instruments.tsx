import { ExternalLink } from 'lucide-react';
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
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { AssetClass, Source, SOURCE_LABELS } from '@/lib/instrumentTypes';
import { BaseResolution, RESOLUTION_LABELS } from '@/lib/resolutions';
import { trpc } from '@/lib/trpc';

const BASE_RESOLUTIONS: BaseResolution[] = ['1_minute', '5_minute', '1_hour', '1_day'];

const RESOLUTION_SHORT_LABELS: Record<BaseResolution, string> = {
  '1_minute': '1m',
  '5_minute': '5m',
  '1_hour': '1h',
  '1_day': '1d'
};

// Calendar depends on what actually trades, not which source it came from -- a Yahoo/IB future
// (e.g. NQ) trades ~23h/day on CME Globex, nothing like NYSE's 9:30-16:00 session. Getting this
// wrong doesn't just miscount coverage: gap-detection re-flags the "missing" off-NYSE-hours data
// as gaps forever, since it can never be filled.
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
  const [description, setDescription] = useState('');
  // Every resolution to fetch directly from the source -- e.g. an IB future collecting both
  // '5_minute' and '1_day' directly, since daily depth vastly exceeds intraday depth and deriving
  // one from the other would be wrong in either direction. Defaults to 1 hour alone: cheaper to
  // store/query than 1-minute, while the live refresh job still keeps the current hour's bar
  // updating every minute rather than only on the hour.
  const [resolutions, setResolutions] = useState<BaseResolution[]>(['1_hour']);
  const [backfillFullHistory, setBackfillFullHistory] = useState(false);

  const toggleResolution = (value: BaseResolution) => {
    setResolutions(prev =>
      prev.includes(value)
        ? prev.filter(r => r !== value)
        : [...prev, value].sort((a, b) => BASE_RESOLUTIONS.indexOf(a) - BASE_RESOLUTIONS.indexOf(b))
    );
  };

  // Debounced so a source with a strict request-pacing budget (IB) doesn't queue up a doomed
  // lookup for every intermediate keystroke -- see useDebouncedValue's comment.
  const debouncedQuery = useDebouncedValue(query, 350);
  const search = trpc.instruments.searchSymbols.useQuery(
    { source, query: debouncedQuery },
    { enabled: debouncedQuery.length > 0 }
  );

  const register = trpc.instruments.register.useMutation({
    onSuccess: () => {
      setSelected(null);
      setQuery('');
      setDescription('');
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
            <SelectItem value="binance">{SOURCE_LABELS.binance}</SelectItem>
            <SelectItem value="yahoo">{SOURCE_LABELS.yahoo}</SelectItem>
            <SelectItem value="ib">{SOURCE_LABELS.ib}</SelectItem>
            <SelectItem value="databento">{SOURCE_LABELS.databento}</SelectItem>
          </SelectContent>
        </Select>

        <Input
          className="flex-1"
          placeholder="Search symbol (e.g. BTCUSDT, AAPL, NQ)"
          value={query}
          onChange={e => {
            setQuery(e.target.value);
            setSelected(null);
          }}
        />
      </div>

      <Input
        placeholder="Description (optional -- e.g. E-mini Nasdaq-100 Futures)"
        value={description}
        onChange={e => setDescription(e.target.value)}
      />

      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Resolutions to collect directly</Label>
        <div className="flex flex-wrap gap-1">
          {BASE_RESOLUTIONS.map(value => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={resolutions.includes(value) ? 'default' : 'outline'}
              onClick={() => toggleResolution(value)}
            >
              {RESOLUTION_LABELS[value]}
            </Button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Switch id="backfill-full-history" checked={backfillFullHistory} onCheckedChange={setBackfillFullHistory} />
        <Label htmlFor="backfill-full-history" className="text-sm font-normal text-muted-foreground">
          Backfill full history since inception (instead of the last 30 days)
        </Label>
      </div>

      {debouncedQuery && search.isLoading ? (
        <p className="text-sm text-muted-foreground">Searching…</p>
      ) : null}

      {debouncedQuery && search.error ? (
        <p className="text-sm text-destructive">{search.error.message}</p>
      ) : null}

      {debouncedQuery && search.data && search.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No matches.</p>
      ) : null}

      {debouncedQuery && search.data && search.data.length > 0 ? (
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
        disabled={!selected || resolutions.length === 0 || register.isPending}
        onClick={() =>
          selected &&
          register.mutate({
            source,
            sourceSymbol: selected.symbol,
            displaySymbol: selected.displaySymbol,
            description: description || undefined,
            assetClass: selected.assetClass as AssetClass,
            baseCurrency: selected.baseCurrency,
            quoteCurrency: selected.quoteCurrency,
            resolutions,
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

const EditableDescription = ({
  instrumentId,
  description,
  onSaved
}: {
  instrumentId: string;
  description: string | null;
  onSaved: () => void;
}) => {
  const [value, setValue] = useState(description ?? '');
  const updateDescription = trpc.instruments.updateDescription.useMutation({ onSuccess: onSaved });

  return (
    <Input
      className="text-sm"
      placeholder="No description"
      value={value}
      onChange={e => setValue(e.target.value)}
      onBlur={() => {
        if (value !== (description ?? '')) {
          updateDescription.mutate({ id: instrumentId, description: value });
        }
      }}
    />
  );
};

type ResolutionCoverage = {
  cacheFrom: string | null;
  cacheTo: string | null;
  status: string;
  earliestAvailableDate: string | null;
};
type CollectedResolutions = Record<string, ResolutionCoverage | undefined>;

interface InstrumentSummary {
  id: string;
  displaySymbol: string;
  sourceSymbol: string;
  source: string;
  description: string | null;
  collectedResolutions: CollectedResolutions;
  latestPrice: number | null;
  pointValue: number;
}

const formatPrice = (price: number): string => price.toLocaleString(undefined, { maximumFractionDigits: 4 });

const formatUsd = (value: number): string =>
  value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const ResolutionRow = ({
  instrumentId,
  displaySymbol,
  resolution,
  coverage,
  onChanged
}: {
  instrumentId: string;
  displaySymbol: string;
  resolution: BaseResolution;
  coverage: ResolutionCoverage;
  onChanged: () => void;
}) => {
  const resetResolution = trpc.instruments.resetResolution.useMutation({ onSuccess: onChanged });

  return (
    <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="font-medium">{RESOLUTION_LABELS[resolution]}</span>
          <Badge variant={coverage.status === 'finished' ? 'default' : 'secondary'}>{coverage.status}</Badge>
        </div>
        <span className="text-xs text-muted-foreground">
          {coverage.cacheFrom ? new Date(coverage.cacheFrom).toLocaleDateString() : '—'} →{' '}
          {coverage.cacheTo ? new Date(coverage.cacheTo).toLocaleString() : '—'}
          {coverage.earliestAvailableDate
            ? ` · earliest: ${new Date(coverage.earliestAvailableDate).toLocaleDateString()}`
            : ''}
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        disabled={resetResolution.isPending && resetResolution.variables?.id === instrumentId}
        onClick={() => {
          if (
            window.confirm(
              `Reset ${RESOLUTION_LABELS[resolution]} for ${displaySymbol}? This deletes its cached candle data and re-downloads it.`
            )
          ) {
            resetResolution.mutate({ id: instrumentId, resolution });
          }
        }}
      >
        Reset
      </Button>
    </div>
  );
};

const AddResolutionControl = ({
  instrumentId,
  missing,
  onChanged
}: {
  instrumentId: string;
  missing: BaseResolution[];
  onChanged: () => void;
}) => {
  const [value, setValue] = useState<BaseResolution | ''>('');
  const addResolution = trpc.instruments.addResolution.useMutation({
    onSuccess: () => {
      setValue('');
      onChanged();
    }
  });

  if (missing.length === 0) return null;

  return (
    <div className="flex items-center gap-2">
      <Select value={value} onValueChange={v => setValue(v as BaseResolution)}>
        <SelectTrigger className="flex-1">
          <SelectValue placeholder="Add a resolution…" />
        </SelectTrigger>
        <SelectContent>
          {missing.map(r => (
            <SelectItem key={r} value={r}>
              {RESOLUTION_LABELS[r]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        disabled={!value || addResolution.isPending}
        onClick={() => value && addResolution.mutate({ id: instrumentId, resolution: value })}
      >
        Add
      </Button>
    </div>
  );
};

/** Everything that isn't a single quick action (Live toggle, Refresh, Delete) lives here instead
 * of inline in the table -- description editing and per-resolution reset/add controls made every
 * row multiple lines tall and turned the table into a form-in-a-grid. */
const InstrumentManageDialog = ({
  instrument,
  open,
  onOpenChange,
  onChanged
}: {
  instrument: InstrumentSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) => {
  if (!instrument) return null;

  const collectedKeys = Object.keys(instrument.collectedResolutions) as BaseResolution[];
  const missing = BASE_RESOLUTIONS.filter(r => !collectedKeys.includes(r));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{instrument.displaySymbol}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground">Description</Label>
            <EditableDescription instrumentId={instrument.id} description={instrument.description} onSaved={onChanged} />
          </div>

          <div className="flex flex-col gap-2">
            <Label className="text-xs text-muted-foreground">Resolutions</Label>
            {collectedKeys.map(resolution => (
              <ResolutionRow
                key={resolution}
                instrumentId={instrument.id}
                displaySymbol={instrument.displaySymbol}
                resolution={resolution}
                coverage={instrument.collectedResolutions[resolution]!}
                onChanged={onChanged}
              />
            ))}
            <AddResolutionControl instrumentId={instrument.id} missing={missing} onChanged={onChanged} />
          </div>
        </div>
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
  const refresh = trpc.instruments.refresh.useMutation();
  const deleteInstrument = trpc.instruments.delete.useMutation({
    onSuccess: () => utils.instruments.list.invalidate()
  });

  const [manageId, setManageId] = useState<string | null>(null);
  const invalidate = () => utils.instruments.list.invalidate();
  const managedInstrument = (instruments.data ?? []).find(i => i.id === manageId) as InstrumentSummary | undefined;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Instruments</h1>
        <AddInstrumentDialog onRegistered={invalidate} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Last price</TableHead>
            <TableHead>Description</TableHead>
            <TableHead>Resolutions</TableHead>
            <TableHead>Live</TableHead>
            <TableHead>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(instruments.data ?? []).map(instrument => {
            const collected = instrument.collectedResolutions as CollectedResolutions;
            const collectedKeys = Object.keys(collected) as BaseResolution[];

            return (
              <TableRow
                key={instrument.id}
                className="cursor-pointer"
                onClick={() => navigate(`/instruments/${instrument.id}`)}
              >
                <TableCell>
                  <div className="flex items-center gap-1.5">
                    {instrument.displaySymbol}
                    {instrument.source === 'yahoo' ? (
                      <a
                        href={`https://finance.yahoo.com/quote/${encodeURIComponent(instrument.sourceSymbol)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={e => e.stopPropagation()}
                        title="View on Yahoo Finance"
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell>{SOURCE_LABELS[instrument.source as Source]}</TableCell>
                <TableCell>
                  {instrument.latestPrice === null ? (
                    '—'
                  ) : (
                    <div className="flex flex-col">
                      <span>{formatPrice(instrument.latestPrice)}</span>
                      {/* Only shown when the raw quote isn't already a dollar price (e.g. index
                          futures quoted in abstract points) -- see pointValues.ts on the backend. */}
                      {instrument.pointValue !== 1 ? (
                        <span className="text-xs text-muted-foreground">
                          {formatUsd(instrument.latestPrice * instrument.pointValue)}
                        </span>
                      ) : null}
                    </div>
                  )}
                </TableCell>
                <TableCell className="max-w-48 truncate text-sm text-muted-foreground">
                  {instrument.description || '—'}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {collectedKeys.map(resolution => (
                      <Badge key={resolution} variant={collected[resolution]!.status === 'finished' ? 'default' : 'secondary'}>
                        {RESOLUTION_SHORT_LABELS[resolution]}
                      </Badge>
                    ))}
                  </div>
                </TableCell>
                <TableCell onClick={e => e.stopPropagation()}>
                  <Switch
                    checked={instrument.cacheLivePrices}
                    onCheckedChange={checked => updateFlags.mutate({ id: instrument.id, cacheLivePrices: checked })}
                  />
                </TableCell>
                <TableCell onClick={e => e.stopPropagation()}>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => setManageId(instrument.id)}>
                      Manage
                    </Button>
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
            );
          })}
        </TableBody>
      </Table>

      <InstrumentManageDialog
        instrument={managedInstrument ?? null}
        open={manageId !== null}
        onOpenChange={open => !open && setManageId(null)}
        onChanged={invalidate}
      />
    </div>
  );
};
