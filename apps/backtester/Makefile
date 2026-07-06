.PHONY: setup lint format test

setup:
	uv sync --extra dev

lint:
	ruff check . && mypy src

format:
	ruff format .

test:
	pytest -q
