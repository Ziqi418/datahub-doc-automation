# Orders field validation warning

This document intentionally references a field that does not exist in the
current `fct_orders` schema. It is only for validating the confirmation flow.

```sql
select fct_orders.not_a_field
from fct_orders
```

Expected result: after selecting `fct_orders`, schema evidence marks
`fct_orders.not_a_field` as **unresolved** and high risk. Publishing returns
an error until **Confirm warning** is clicked. The confirmation does not alter
this source document or the DataHub Dataset schema.
