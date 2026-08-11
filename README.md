# DataHub Document Enrichment Agent

Review-first MVP that recommends existing DataHub metadata for a Markdown or TXT document. This repository implements the demo metadata, read-only catalog, deterministic retrieval, constrained LLM ranking, workflow/review API, and safe DataHub Document publishing (implementation-plan phases 1–5 and 7).

## Demo

[Watch the Loom demo](https://www.loom.com/share/f6b3e42b91424f72bf9772b49abbce35)

## What it does

Documentation often describes important data assets without being connected to
the catalog that governs them. DataHub Document Enrichment Agent closes that
gap: it analyzes a Markdown or text document, finds relevant metadata already
stored in DataHub, and proposes the relationships for a human to review before
anything is published.

### Features and functionality

- **Document analysis:** uploads UTF-8 Markdown or plain-text documents and
  extracts candidate datasets, schema fields, glossary terms, tags, domains,
  and owners.
- **Catalog-grounded recommendations:** searches a read-only DataHub catalog
  using deterministic rules, with an optional constrained LLM ranking step.
  Every LLM suggestion is validated against the retrieved DataHub candidates.
- **Human-in-the-loop review:** exposes a review workflow/API so a reviewer can
  accept, remove, or resolve recommendations and conflicts before publishing.
- **Safe Document publishing:** creates an `UNPUBLISHED` native DataHub
  Document, verifies the selected metadata by read-back, then publishes using
  a stable URN so retries remain idempotent.
- **Metadata-change awareness:** a read-only freshness endpoint compares the
  published document's tracked baseline with related datasets and marks it
  `NEEDS_REVIEW` if a dataset disappears or its schema/metadata changes.
- **Local, repeatable demo:** Docker Compose brings up DataHub, seed data, the
  API, and the web UI together. Rule-based recommendations work without an
  LLM API key.

### How it works

1. A user uploads a `.md` or `.txt` business document.
2. The backend extracts references and retrieves a bounded set of matching
   DataHub assets.
3. Rules—and, when configured, an OpenAI-compatible model—rank the matches.
4. The user reviews the recommendations and resolves any conflicts.
5. The approved result is published as a native DataHub Document and can later
   be checked for metadata freshness.

## Technology stack

- **Backend:** Python 3.11, FastAPI, Pydantic, `uv`, and SQLite with versioned
  `dbmate` migrations.
- **Data catalog:** DataHub 1.6.0, accessed through read-only GraphQL catalog
  queries for retrieval and DataHub's native Document publishing APIs.
- **Recommendation layer:** deterministic Python rules plus an optional
  OpenAI-compatible LLM integration, with schema validation and candidate
  grounding safeguards.
- **Frontend:** React, TypeScript, Vite, Mantine, and Tabler icons; tested with
  Vitest, Testing Library, and Playwright.
- **Infrastructure:** Docker Compose orchestrates DataHub and its supporting
  Kafka, Schema Registry, MySQL, Elasticsearch, and Neo4j services alongside
  the application.

## Demo data

The included demo uses a fixed `jaffle_shop` production namespace. It seeds
eleven representative retail datasets—such as `customers`, `orders`,
`payments`, `refunds`, `fct_orders`, and `daily_sales`—with schemas,
descriptions, domains, owners, and tags. The catalog includes the Finance,
Customer, and Operations domains; Finance Analytics, Customer Analytics, and
Data Platform teams; and tags for revenue, payments, PII, data quality,
runbooks, policies, and sales.

Eight Markdown documents model common data-documentation use cases, including
revenue recognition, customer PII policy, payment failure runbooks, refund
reconciliation, data quality, and sales operations. Gold YAML annotations and
manual-validation scenarios are included to evaluate recommendation quality,
schema resolution, conflict review, and freshness baselines.

## Docker demo (recommended for judges)

With Docker Desktop running, start the complete demo (DataHub, demo metadata,
API, and UI) with one command:

```sh
docker compose up --build
```

Wait for the `seed-demo` container to complete, then open
<http://localhost:5173>. DataHub itself is available at
<http://localhost:9002>. The Docker demo uses deterministic rule-based
recommendations when no LLM credentials are supplied, so it is usable without
an API key. To erase all Docker demo data, run `docker compose down -v`.

To use your own OpenAI-compatible model locally, add `LLM_API_KEY` and
`LLM_MODEL` to the untracked `.env` file, then restart the API with
`docker compose up -d --force-recreate api`. The Compose file passes these
values only to the local API container; `.env` is gitignored and must not be
committed.

## Local development

1. Copy `.env.example` to `.env` and point it at a running local DataHub v1.6.0 instance.
2. Install dbmate: `brew install dbmate`.
3. Install the backend dependencies: `make install`.
4. Apply the local versioned schema: `make migrate`.
5. Validate local fixtures without DataHub: `cd backend && uv run python ../scripts/verify_demo_data.py --offline`.
6. Seed only the fixed Jaffle Shop namespace: `make seed-demo`.
7. Verify seed data via GraphQL: `make verify-demo-data`.
8. Configure `LLM_API_KEY` and `LLM_MODEL`, then run the API with `make run`.

`make seed-demo` is idempotent and only writes the `jaffle_shop` platform datasets plus the fixed `finance`, `customer`, `operations`, tag, and group demo URNs. It does not delete or modify unrelated DataHub assets.

## Safety boundaries

- Uploads accept only UTF-8 `.md` / `.txt`, max 256 KiB and 30,000 characters.
- DataHub catalog requests are read-only GraphQL calls with a five-minute cache and bounded result lists.
- LLM output is schema-validated and checked again against candidates from DataHub before it can become a recommendation.
- Publishing only starts after a saved review. The publisher writes an `UNPUBLISHED` native Document, verifies every selected field by read-back, then publishes it using the same stable URN on retries.
- `POST /api/analyses/{id}/freshness` is read-only against DataHub. It marks a locally tracked document `NEEDS_REVIEW` when a related Dataset is missing or its schema/metadata baseline changes; it never rewrites or deletes the Document.
- The SQLite schema is versioned under `backend/db/migrations`; run `make migrate` before the API starts.

## Local checks

```sh
make lint
make test
make migrate
make migration-status
make check-env       # requires running DataHub
```
