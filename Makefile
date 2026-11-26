.PHONY: install lint test test-all up down demo models schemas

install:        ## Install all workspace packages and dev tools
	uv sync

models:         ## Download on-device ML models (face, pose, audio)
	uv run autoedit models download

lint:           ## Format check, lint, type check
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy packages/schemas/src packages/pipeline/src apps/api/src

test:           ## Unit, media regression and API tests (no services needed)
	uv run pytest -m "not integration"

test-all:       ## Everything, including Postgres/Redis integration (needs AUTOEDIT_IT_* env vars)
	uv run pytest

up:             ## Full stack: api, worker, postgres, redis, minio
	docker compose -f infra/docker-compose.yml up --build

down:
	docker compose -f infra/docker-compose.yml down

schemas:        ## Export JSON Schemas for the web client
	uv run autoedit schema export packages/schemas/json

demo:           ## Analyze the synthetic ground-truth clip
	uv run python -c "from pathlib import Path; from autoedit_pipeline.synth import make_storyboard_clip as m; m(Path('data/samples/storyboard.mp4'))"
	uv run autoedit analyze data/samples/storyboard.mp4 -o autoedit-out
