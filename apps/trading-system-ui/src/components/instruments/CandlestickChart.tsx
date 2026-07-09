import {
  CandlestickData,
  CandlestickSeries,
  createChart,
  IChartApi,
  ISeriesApi,
  LogicalRange,
  UTCTimestamp
} from 'lightweight-charts';
import { useEffect, useRef } from 'react';

export interface Candle {
  timeOpen: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

// Once the visible range's left edge gets within this many bars of the start of loaded data,
// fetch the next chunk further back -- small enough to not fire on every minor pan, large enough
// that more data is usually in place before the user actually scrolls off the edge of the chart.
const LOAD_MORE_LOGICAL_THRESHOLD = 20;

const toCandlestickData = (c: Candle): CandlestickData => ({
  time: (new Date(c.timeOpen).getTime() / 1000) as UTCTimestamp,
  open: c.open,
  high: c.high,
  low: c.low,
  close: c.close
});

// lightweight-charts throws an uncaught exception (crashing the whole page, since nothing here
// catches it) if `setData` ever receives rows that aren't strictly ascending by time. The
// intended data flow (see useCandleHistory) always prepends strictly-older chunks, so this
// shouldn't normally be needed -- but sorting + deduping defensively here means a data-layer edge
// case (e.g. two overlapping fetches racing, a resolution with sparse/gappy source coverage)
// degrades to "a chart that's momentarily slightly off" instead of "a blank white page".
const toSortedCandlestickData = (data: Candle[]): CandlestickData[] => {
  const byTime = new Map<number, CandlestickData>();
  for (const c of data) {
    const point = toCandlestickData(c);
    byTime.set(point.time as number, point);
  }
  return [...byTime.values()].sort((a, b) => (a.time as number) - (b.time as number));
};

export const CandlestickChart = ({
  data,
  liveCandle,
  height = 320,
  onLoadMoreHistory
}: {
  data: Candle[];
  liveCandle?: Candle | null;
  height?: number;
  /** Called when the user scrolls/pans near the earliest loaded bar. May be called more than
   * once in a row -- the caller (see useCandleHistory) is responsible for ignoring calls while
   * a load is already in flight or there's nothing more to load. */
  onLoadMoreHistory?: () => void;
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const onLoadMoreHistoryRef = useRef(onLoadMoreHistory);
  const prevDataLengthRef = useRef(0);
  const hasFitInitialDataRef = useRef(false);

  useEffect(() => {
    onLoadMoreHistoryRef.current = onLoadMoreHistory;
  }, [onLoadMoreHistory]);

  // Chart + series are created once and torn down on unmount -- data/liveCandle are pushed in
  // via separate effects below rather than recreating the chart on every update.
  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const chart: IChartApi = createChart(containerRef.current, {
      height,
      width: containerRef.current.clientWidth,
      layout: { background: { color: 'transparent' }, textColor: '#888' },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(128,128,128,0.15)' } },
      timeScale: { timeVisible: true, secondsVisible: false }
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444'
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    const handleVisibleLogicalRangeChange = (range: LogicalRange | null) => {
      if (range && range.from < LOAD_MORE_LOGICAL_THRESHOLD) {
        onLoadMoreHistoryRef.current?.();
      }
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleVisibleLogicalRangeChange);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleVisibleLogicalRangeChange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current || data.length === 0) {
      return;
    }

    const sorted = toSortedCandlestickData(data);

    // First data this chart instance has ever seen -- fit the whole thing into view. Every
    // subsequent update (within this mount) is older bars prepended by scroll-back loading, so
    // instead of re-fitting (which would yank the user back to showing everything), shift the
    // visible logical range by however many bars were just added to the front, keeping the same
    // bars on screen.
    if (!hasFitInitialDataRef.current) {
      seriesRef.current.setData(sorted);
      chartRef.current?.timeScale().fitContent();
      hasFitInitialDataRef.current = true;
    } else {
      const addedBars = sorted.length - prevDataLengthRef.current;
      const priorRange = chartRef.current?.timeScale().getVisibleLogicalRange() ?? null;
      seriesRef.current.setData(sorted);
      if (priorRange && addedBars > 0) {
        chartRef.current?.timeScale().setVisibleLogicalRange({
          from: priorRange.from + addedBars,
          to: priorRange.to + addedBars
        });
      }
    }
    prevDataLengthRef.current = sorted.length;
  }, [data]);

  useEffect(() => {
    if (!seriesRef.current || !liveCandle) {
      return;
    }
    seriesRef.current.update(toCandlestickData(liveCandle));
  }, [liveCandle]);

  return <div ref={containerRef} />;
};
