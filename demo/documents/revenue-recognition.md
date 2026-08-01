# Revenue Recognition Policy

This policy defines when the Jaffle Shop finance team records revenue. Revenue is recognized when an order is completed and a successful payment has been captured. Gross revenue is kept separate from refunds so monthly reporting can show both the original sale and the final net revenue.

The canonical reporting model is `fct_orders`. It contains `gross_revenue`, `payment_amount`, `refund_amount`, and `net_revenue` at order grain. Analysts must use net revenue for executive reporting and must not infer it from a raw payment attempt alone. Finance Analytics owns changes to the policy and to the metric definition.

```sql
select order_id, gross_revenue, refund_amount, net_revenue
from fct_orders
where order_date >= date_trunc('month', current_date)
```

Payments are matched by `payments.payment_id`; refunds are matched through `refunds.payment_id` and `refunds.refund_amount`. A late refund adjusts the period in which the refund is processed, while the order continues to show its original gross amount. Any exception must be documented with the payment and refund identifiers so Finance Analytics can reconcile the result.
