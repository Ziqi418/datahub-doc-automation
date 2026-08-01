# Daily Sales Dashboard Guide

The Daily Sales dashboard is the operational view of store performance. Its primary source is `daily_sales`, aggregated by `sales_date` and `store_id`. It shows order count, gross revenue, and net revenue. Store names and regions come from `stores`; order drill-downs come from `orders`.

```sql
select sales_date, store_id, order_count, net_revenue
from daily_sales
where sales_date >= current_date - interval '30 days'
```

Finance Analytics owns metric definitions while Data Platform monitors freshness. A missing store row must be reported as a data-quality incident, not filled with a guessed region. For the dashboard, net revenue includes processed refunds, so comparisons with gross sales should explain the difference. Filter by store region only after joining the official `stores` directory.
