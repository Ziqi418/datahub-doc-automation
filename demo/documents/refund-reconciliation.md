# Refund Reconciliation Guide

Refund reconciliation confirms that processed refunds reduce net revenue exactly once. Finance Analytics compares `refunds` with `payments` and the finance-ready `fct_orders` model. The primary keys are `refund_id`, `payment_id`, and `order_id`; the key amounts are `refund_amount`, `payment_amount`, and `net_revenue`.

```sql
select r.order_id, r.refund_amount, f.net_revenue
from refunds r
join fct_orders f on f.order_id = r.order_id
where r.refunded_at >= date_trunc('month', current_date)
```

A refund without a matching successful payment must be investigated before close. A payment can have multiple partial refunds, but the total refund must not exceed the captured amount. Record late-arriving refunds in the current reconciliation log and explain their impact on the monthly variance. This is a finance control, not a replacement for the payment-failure incident process.
