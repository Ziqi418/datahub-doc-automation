# Orders revenue field validation

This guide validates the net revenue field used by Finance reporting.

```sql
select fct_orders.net_revenue
from fct_orders
```

Expected result: after selecting `fct_orders` as a Related Dataset, the schema
evidence marks `fct_orders.net_revenue` as **resolved** and publishing is not
blocked by schema validation.
