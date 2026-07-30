# DataHub Document Enrichment Agent

Review-first MVP that recommends existing DataHub metadata for a Markdown or TXT document. This repository implements the demo metadata, read-only catalog, deterministic retrieval, constrained LLM ranking, and workflow/review API (implementation-plan phases 1–5). DataHub Document publishing and the full review UI intentionally begin in later phases.

## Quick start

1. Copy `.env.example` to `.env` and point it at a running local DataHub v1.6.0 instance.
2. Install the backend dependencies: `make install`.
3. Validate local fixtures without DataHub: `cd backend && uv run python ../scripts/verify_demo_data.py --offline`.
4. Seed only the fixed Jaffle Shop namespace: `make seed-demo`.
5. Verify seed data via GraphQL: `make verify-demo-data`.
6. Configure `LLM_API_KEY` and `LLM_MODEL`, then run the API with `make run`.

`make seed-demo` is idempotent and only writes the `jaffle_shop` platform datasets plus the fixed `finance`, `customer`, `operations`, tag, and group demo URNs. It does not delete or modify unrelated DataHub assets.

## Safety boundaries

- Uploads accept only UTF-8 `.md` / `.txt`, max 256 KiB and 30,000 characters.
- DataHub catalog requests are read-only GraphQL calls with a five-minute cache and bounded result lists.
- LLM output is schema-validated and checked again against candidates from DataHub before it can become a recommendation.
- Phase 5 has no DataHub publishing endpoint. Saving an audit/review never writes a DataHub Document.

## Local checks

```sh
make lint
make test
make check-env       # requires running DataHub
```
