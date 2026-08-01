# Order Data Quality Rules

This runbook defines quality checks for order ingestion. Every `orders.order_id` must be unique, every order must reference an existing `customers.customer_id`, and each record must have a valid `order_date` and status. Order line data in `order_items` must use a known `products.product_id` and a positive quantity.

```sql
select o.order_id
from orders o
left join order_items i on i.order_id = o.order_id
where o.status = 'completed' and i.order_item_id is null
```

Data Platform owns monitoring and escalation. Alert when the completed-order-to-line-item check fails, when duplicate order IDs appear, or when the daily row count deviates from its seven-day baseline. The purpose of these checks is operational reliability; they do not change financial revenue recognition. Incidents should include sample order IDs, affected dates, and the last successful pipeline run.
