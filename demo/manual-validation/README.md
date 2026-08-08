# Stage 7.7 manual validation documents

Upload the numbered files in order as needed; each is independent.

| File | Select in Review | What it verifies |
| --- | --- | --- |
| `01-schema-resolved.md` | `fct_orders` | Explicit valid SQL field resolves without a blocking warning. |
| `02-schema-unresolved.md` | `fct_orders` | Invalid explicit SQL field blocks publish until explicitly confirmed. |
| `03-revenue-metric-conflict.md` | `fct_orders` | Conflict preview and the publish-result conflict summary. Requires an existing similar DataHub Document to find a candidate. |
| `04-freshness-baseline.md` | `fct_orders` | Publish baseline, then manually modify Dataset metadata/schema and run freshness. |

For the conflict case, an existing DataHub Document needs both similar wording
(for example “Revenue metric definition” plus “metric formula”) and the same
`fct_orders` related Dataset. The application deliberately does not create or
modify that existing Document for this test.
