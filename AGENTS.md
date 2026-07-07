# AGENTS.md

Context for any AI coding agent (or human) working in this repo. Read this before making changes.

## What this is

`alphakit` is a systematic trading system. It started as `legacy-trading-system/` (crypto-only,
CCXT + TimescaleDB, moving-average-crossover strategies) and is being rebuilt fresh as a
source-agnostic prop-trading platform: multi-asset market data aggregation now, backtesting and
live execution later. The rebuild is a deliberate fresh start, not an in-place migration of the
legacy code.

## Repo layout

```
apps/
  trading-system-backend/   Node/TS API + data pipeline (see below)
  trading-system-ui/        React 19 frontend for trading-system-backend
  backtester/                Python backtesting engine (uv-managed, separate from the JS apps)
legacy-trading-system/       OLD crypto-only system. Reference only -- not part of the pnpm or uv
                             workspace, not under active development. Don't extend it; if similar
                             logic is needed in the new system, reimplement it there.
notebooks/                   Exploratory Python (Jupyter) -- data analysis, not shipped code.
datasets/                    Local data files used by notebooks/backtester.
infra/                       docker-compose.yml (Mongo, Redis, TimescaleDB) + Mongo init scripts.
```

Node side is a pnpm workspace (`pnpm-workspace.yaml`: `apps/*`, `packages/*`); Python side is a uv
workspace (`pyproject.toml`, member: `apps/backtester`) with a single shared root `.venv`.

## trading-system-backend

Express 5 + tRPC v11 + MongoDB (config/state) + TimescaleDB (OHLCV) + BullMQ + node-cron + Redis.

- **Data model**: `instruments` (Mongo) hold source/symbol/resolution/coverage metadata;
  `candles__{1_minute,15_minute,1_hour,1_day}` (TimescaleDB hypertables, one per resolution,
  partitioned on `instrument_id`) hold OHLCV. An instrument has a `baseResolution` (what's
  actually fetched from the source); everything coarser is derived in SQL from that base data.
  `earliestAvailableDates` on the instrument doc caches the source's earliest data per resolution
  (looked up lazily, only for resolutions actually used -- see gotchas).
- **Connectors** (`src/connectors/`): `MarketDataConnector` interface, implemented for `binance`
  (public REST, no auth) and `yahoo` (unofficial `yahoo-finance2`, rate-limited, resolution
  fallback for old intraday ranges). Adding a new source (Tradovate/IB planned) means implementing
  this interface, not touching callers.
- **Jobs/scheduling** (`src/jobs/`, `src/queue/`, `src/cron/`): BullMQ queues
  (`MARKET_DATA_LIVE/HISTORICAL/GAPS`) processed by `queue/workers.ts`; node-cron in `cron/cron.ts`
  fans out per-instrument jobs every 1min (refresh) / 5min (gap-fill), guarded by a Redis lock
  (`runCodeBlockOnOneServerOnly`) so multiple replicas don't double-run a tick.
- **No WebSockets anywhere, by design.** Live price updates reach the frontend via a tRPC
  subscription over SSE, bridged off a Redis pub/sub channel (`candles:{instrumentId}:{resolution}`)
  that `refreshLatestCandles` publishes to on each cron tick.
- **Auth**: JWT access token (in-memory on the frontend) + httpOnly refresh cookie, with a
  `tokenVersion` field on the user doc so logout invalidates outstanding tokens immediately.
  `trpc/context.ts` falls back from Bearer token to the refresh cookie specifically because the
  SSE subscription (native `EventSource`) can't send custom headers. Bull Board (`/admin/queues`)
  is gated the same way (`requireValidRefreshCookie`), since a plain `<a target="_blank">` link
  can't carry a Bearer token either but does send cookies.
- **Graceful shutdown** (`src/shutdown.ts`): SIGTERM/SIGINT close cron, BullMQ workers/queues, the
  HTTP server, then Redis/Mongo/Timescale, then `process.exit()`. This isn't optional ceremony --
  without it the process can't exit on its own and the dev watcher (or Docker) has to SIGKILL it.
- **API schema browser** (`/docs/api`, dev-only, same auth gate as Bull Board): a small
  self-built page (`src/admin/apiDocs.ts`), *not* a third-party package -- reads tRPC's own
  `appRouter._def.procedures` (path/type/input schema per procedure) and renders each input via
  zod v4's own `z.toJSONSchema()`. No "try it" buttons, just real field-level schemas grouped by
  router. Built this way specifically because every third-party tRPC-schema-UI package introspects
  zod's *internal* `_def` shape and breaks on zod v4 (see gotchas) -- this only ever calls zod's
  public API, on schema objects tRPC handed us directly, so it isn't exposed to that.

## trading-system-ui

React 19 + React Router 7 + `@trpc/react-query` + Tailwind + shadcn/radix-ui + `lightweight-charts` v5.

- `lib/trpc.ts` splits queries/mutations (`httpBatchLink`) from subscriptions
  (`httpSubscriptionLink`, `withCredentials: true` for the SSE cookie fallback above).
- Chart data loading (`hooks/useCandleHistory.ts`) fetches an initial window and loads further
  back on scroll; "is there more history" is decided by comparing against the instrument's known
  earliest-available date, **not** by counting rows per fetch (a fixed calendar-time window
  returns very different row counts for a 24/7 market vs. one with weekends/holidays).
- **State-reset pattern**: when a component's identity should fully reset on a prop change (e.g. a
  chart's live-price state when switching instrument/resolution), prefer a `key` remount over an
  effect that calls `setState` on every dependency change. The latter trips
  `react-hooks/set-state-in-effect` and causes an extra render for no benefit; see
  `CandlestickChart`/`ChartForResolution` for the established pattern.

## Getting started

```
make setup          # uv sync (backtester) + pnpm install
make infra-up        # Mongo (27018) + Redis (63794) + TimescaleDB (5432) via docker compose
pnpm --filter trading-system-backend migrate:timescale
pnpm --filter trading-system-backend seed:calendars
pnpm --filter trading-system-backend seed:user -- --email <email> --password <password>
make dev-backend      # tsx watch, http://localhost:4000
make dev-ui           # vite, http://localhost:5173
```

Backend prints all its own URLs (API, tRPC endpoint, health check, Bull Board, dev frontend) on
startup -- check that output rather than hardcoding ports elsewhere.

Copy `apps/trading-system-backend/.env.example` to `.env` and fill in secrets before first run.

## Testing / linting / type-checking

```
pnpm typecheck   # tsc --noEmit for both trading-system-backend and trading-system-ui
pnpm lint        # eslint . (root flat config, apps/backtester and legacy-trading-system excluded)
pnpm test        # vitest, trading-system-backend only (trading-system-ui has no tests yet)
make test        # pytest for apps/backtester
make lint         # ruff + mypy for apps/backtester
```

Run `pnpm typecheck && pnpm lint && pnpm test` before considering any backend/frontend change
done. There's no CI wired up yet -- this is the only gate.

## Known gotchas (read before you hit these yourself)

- **Express 5 / path-to-regexp v8**: wildcard routes need a named param -- `app.get('/*splat', ...)`,
  not `app.get('*', ...)` (the latter throws at startup).
- **ioredis is pinned to `5.10.1`** (root `package.json` pnpm override + the backend's own exact
  dependency version) because BullMQ hard-pins that version internally; letting it drift causes a
  structural TS incompatibility between the two ioredis instances.
- **zod v4 breaks tools that introspect zod internals.** This project uses zod v4 throughout. Any
  package that reads `_def.typeName` (older zod-v3-era tRPC tooling -- `trpc-ui`/`trpc-panel`,
  `trpc-openapi`, `trpc-to-openapi` as of mid-2026) will crash trying to parse the router. Tried
  `trpc-ui` as a Swagger-UI-for-tRPC equivalent and reverted it for exactly this reason
  (`TypeError: Cannot read properties of undefined (reading 'typeName')`, tracked upstream as an
  open, unresolved issue). Solved instead with a small custom page (`/docs/api`, see above) built
  on tRPC's own `_def.procedures` + zod v4's own `z.toJSONSchema()` -- never touches zod
  internals, so don't reintroduce a third-party schema-UI package without checking this first.
- **lightweight-charts v5** dropped `chart.addCandlestickSeries()`; it's
  `chart.addSeries(CandlestickSeries, options)` now, and `ISeriesApi` has no `.chart()` back-reference
  (keep a separate chart ref if you need both).
- **Yahoo Finance is unofficial and rate-limited.** All calls go through `yahooRateLimited` (one
  in-process queue, 500ms min spacing) and `withRetry`. Gap-detection is deliberately bounded
  (`GAP_CHECK_LOOKBACK_DAYS`, `MAX_GAP_CHUNKS_PER_RUN`) after a real incident where an unbounded
  gap-checker flooded Yahoo with requests for permanently-unfillable historical gaps. Don't remove
  those bounds without replacing them with something equally conservative.
- **Earliest-available-date lookups are scoped to one resolution at a time**, looked up only for
  the resolution actually being registered/switched to (not eagerly for every resolution) --
  looping over every resolution on every registration previously meant a resolution nobody asked
  for could fail the whole registration (e.g. Yahoo rejecting an old intraday range for "1 hour"
  when the user only wanted "1 day").
- **Dev servers accumulate across sessions.** `tsx watch` / `vite` processes started with `&` or
  `nohup` don't get cleaned up automatically and will silently fight over ports 4000/5173 across
  multiple terminal sessions. Check `lsof -ti:4000,5173` and kill stragglers before starting new
  ones, especially before trusting a "can't connect" or flaky-auth symptom.

## Keeping this file up to date

This file goes stale fast if it's not treated as part of the change itself. Whenever you make a
change that would make something above wrong or incomplete -- a new app/package, a changed dev
command, a new footgun worth warning the next session about, a real architectural shift -- update
AGENTS.md in the same piece of work, not as a follow-up. Prefer editing existing sections over
appending; keep it dense enough to be worth reading, not a changelog.
