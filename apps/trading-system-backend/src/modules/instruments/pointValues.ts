// Dollar value of a one-unit move in an instrument's raw quoted price -- 1 for anything where the
// quote already *is* the dollar price (equities, spot/perpetual crypto, most tickers generally).
// Only a handful of CME futures need a real conversion: equity index futures quote an abstract
// index level (multiply by $/point to get notional), and FX/livestock futures quote a per-unit
// price that needs multiplying by the contract's real-world unit count (EUR, lbs, ...) to get
// dollar notional. Values below are CME's own published contract specs, keyed by displaySymbol --
// the same real-world contract regardless of which source (IB/Yahoo) an instrument was registered
// from, so this is shared rather than per-source or stored per-instrument from a connector call.
//
// Livestock (HE/LE/GF) specifically: CME's contract size is in lbs and its spec sheet quotes price
// in dollars/lb, but IB/Yahoo both serve livestock futures quoted in the traditional cents/lb
// convention (e.g. ~99 for lean hogs, not ~0.99) -- confirmed by sanity-checking against
// real-world per-lb prices (lean hogs/live cattle priced in the tens-to-low-hundreds range only
// makes sense as cents/lb). The point value below is therefore contract-size-in-lbs / 100, i.e.
// dollars per 1.00 move of the raw cents-quoted price actually stored, not the raw lbs figure.
export const KNOWN_POINT_VALUES: Record<string, number> = {
  // Equity index futures -- $ per index point.
  ES: 50,
  MES: 5,
  NQ: 20,
  MNQ: 2,
  RTY: 50,
  M2K: 5,
  EMD: 100,
  NKD: 5,
  // FX futures -- contract size in base-currency units; price is already quoted as USD per unit.
  '6E': 125_000,
  M6E: 12_500,
  '6B': 62_500,
  '6J': 12_500_000,
  '6C': 100_000,
  '6S': 125_000,
  '6A': 100_000,
  M6A: 10_000,
  // Livestock -- $ per 1.00 move of the cents/lb quote (see comment above).
  HE: 400,
  LE: 400,
  GF: 500
};

export const pointValueFor = (displaySymbol: string): number => KNOWN_POINT_VALUES[displaySymbol] ?? 1;
