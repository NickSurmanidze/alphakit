.PHONY: setup setup-py setup-js \
	infra-up infra-down infra-logs infra-ps \
	test test-backtester lint lint-backtester format format-backtester \
	dev-backend dev-ui seed-user

## Install Python (uv workspace) and Node (pnpm workspace) dependencies
setup: setup-py setup-js

setup-py:
	uv sync --package backtester --extra dev

setup-js:
	pnpm install

## Local infra: mongo, redis, timescale
infra-up:
	docker compose -f infra/docker-compose.yml up -d

infra-down:
	docker compose -f infra/docker-compose.yml down

infra-logs:
	docker compose -f infra/docker-compose.yml logs -f

infra-ps:
	docker compose -f infra/docker-compose.yml ps

## Tests / lint
test: test-backtester

test-backtester:
	uv run --package backtester pytest -q

lint: lint-backtester

lint-backtester:
	uv run --package backtester ruff check apps/backtester
	uv run --package backtester mypy apps/backtester/src

format: format-backtester

format-backtester:
	uv run --package backtester ruff format apps/backtester

## App dev servers
dev-backend:
	pnpm --filter trading-system-backend dev

dev-ui:
	pnpm --filter trading-system-ui dev

seed-user:
	pnpm --filter trading-system-backend seed:user -- $(ARGS)
