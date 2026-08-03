UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
export UV_CACHE_DIR

.PHONY: install test lint run seed-demo verify-demo-data check-env eval-rules test-llm eval-llm

install:
	cd backend && uv sync --extra dev --extra datahub

test:
	cd backend && uv run --extra dev pytest

lint:
	cd backend && uv run --extra dev ruff check src tests

run:
	cd backend && uv run uvicorn document_enrichment.api.app:app --reload --port 8000

seed-demo:
	cd backend && uv run --extra datahub python ../scripts/seed_demo.py

verify-demo-data:
	cd backend && uv run python ../scripts/verify_demo_data.py

eval-rules:
	cd backend && uv run python ../scripts/evaluate_rules.py

test-llm:
	cd backend && uv run python ../scripts/test_llm_recommendations.py

eval-llm:
	cd backend && uv run python ../scripts/evaluate_llm.py

check-env:
	cd backend && uv run python ../scripts/check_environment.py
