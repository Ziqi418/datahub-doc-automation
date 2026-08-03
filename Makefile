UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
export UV_CACHE_DIR
BACKEND_PORT ?= 8000

.PHONY: install test lint run migrate migration-status new-migration seed-demo verify-demo-data check-env eval-rules test-llm eval-llm

install:
	cd backend && uv sync --extra dev --extra datahub

test:
	cd backend && uv run --extra dev pytest

lint:
	cd backend && uv run --extra dev ruff check src tests

run:
	cd backend && uv run uvicorn document_enrichment.api.app:app --app-dir src --reload --port $(BACKEND_PORT)

migrate:
	mkdir -p backend/data
	cd backend && set -a && test ! -f ../.env || . ../.env; set +a; DBMATE_DATABASE_URL="$${DBMATE_DATABASE_URL:-sqlite:./data/document_enrichment.db}" dbmate --env DBMATE_DATABASE_URL --migrations-dir db/migrations --no-dump-schema up

migration-status:
	cd backend && set -a && test ! -f ../.env || . ../.env; set +a; DBMATE_DATABASE_URL="$${DBMATE_DATABASE_URL:-sqlite:./data/document_enrichment.db}" dbmate --env DBMATE_DATABASE_URL --migrations-dir db/migrations status

new-migration:
	@test -n "$(name)" || (echo "Usage: make new-migration name=add_publish_audit" && exit 1)
	cd backend && dbmate --migrations-dir db/migrations new "$(name)"

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
