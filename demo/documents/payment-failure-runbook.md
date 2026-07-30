# Payment Failure Runbook

Use this runbook when the payment failure rate rises or payment events stop arriving. The source of truth is `payments`, containing `payment_id`, `order_id`, `payment_method`, `payment_status`, `amount`, and `failure_reason`. Start by determining whether failures are isolated to a method or a time range, then compare affected orders in `orders`.

```sql
select payment_method, failure_reason, count(*)
from payments
where payment_status = 'failed'
group by 1, 2
```

Finance Analytics owns payment reporting; Data Platform owns pipeline recovery. Do not mark a failed payment as revenue. If a customer was charged and the subsequent correction is a refund, track the related record in `refunds`. Capture the incident window, error distribution, affected payment IDs, remediation decision, and confirmation that the next successful load restored normal volumes.
