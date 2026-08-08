# Net revenue reporting runbook

Use this document to create a published baseline for the freshness evidence
check. Do not upload it for the unresolved-field workflow.

The dashboard uses the `net_revenue` value from the finalized order fact.

```sql
select
  fct_orders.order_date,
  fct_orders.net_revenue
from fct_orders
```

Expected result: publish after selecting `fct_orders`. Then modify the Dataset
in DataHub (for example the description, an owner/tag/domain, or the
`net_revenue` field type, nullable flag, or description). Click **Check
freshness** on the published result. The UI should show structured old/new
evidence and flag a changed or removed `net_revenue` field as affecting a
referenced field.
