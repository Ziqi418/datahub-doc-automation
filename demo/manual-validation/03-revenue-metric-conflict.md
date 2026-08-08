# Revenue metric definition

This document describes the revenue metric formula and reporting guidance for
the Finance team. It intentionally shares title and metric/formula language
with an existing Revenue metric document, when one is present in DataHub.

```sql
select fct_orders.net_revenue
from fct_orders
```

Expected result: select `fct_orders` and publish. The conflict preview should
show a candidate only if DataHub already contains a similar Document associated
with the same Dataset. A high-risk candidate must be explicitly confirmed.
When the catalog has no matching Document, the result summary must say
`0 conflicts found`.
