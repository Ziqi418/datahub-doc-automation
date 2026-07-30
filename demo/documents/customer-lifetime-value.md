# Customer Lifetime Value Definition

Customer lifetime value (CLV) is the cumulative net value a customer has generated through completed orders. The official model is `customer_lifetime_value`, which stores `customer_id`, `lifetime_value`, `order_count`, `first_order_at`, and `last_order_at`. It is refreshed after the finance-ready order fact is available.

Customer Analytics owns the definition. Use `customers` for customer identity and lifecycle attributes, `orders` for the order lifecycle, and `payments` for the monetary events. Exclude cancelled orders and failed payment attempts. Refunds lower the value through the net order calculation rather than being added as a positive transaction.

```sql
select customer_id, lifetime_value, order_count
from customer_lifetime_value
where last_order_at >= current_date - interval '90 days'
```

The metric is appropriate for retention segmentation, but not for an invoice-level finance close. Do not expose email or address fields from `customers` in CLV exports unless the audience is approved for customer data.
