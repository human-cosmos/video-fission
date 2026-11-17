.PHONY: install dev build test e2e

install:
	uv sync --extra dev
	npm install --prefix frontend

dev:
	./scripts/dev.sh

build:
	npm run build --prefix frontend

test:
	uv run pytest -q
	npm test --prefix frontend

e2e:
	RUN_LIVE_CODEX=1 uv run pytest backend/tests/test_live_codex.py -q
	npm run test:e2e --prefix frontend

