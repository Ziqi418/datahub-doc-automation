# Store Sales Operations Definition

Store sales operations measures the order flow and net sales for active retail locations. `daily_sales` is the dashboard aggregate; `stores` supplies the store directory, region, manager, and status; and `orders` provides the order-level drill-through. Operational teams use the measure to identify unusually low order volume and to coordinate store follow-up.

```sql
select s.region, d.sales_date, sum(d.order_count) as order_count
from daily_sales d
join stores s on s.store_id = d.store_id
where s.status = 'active'
group by 1, 2
```

Do not treat an inactive store as a zero-sales active store. Check `stores.status` first and raise a data-quality issue if an active store disappears from the daily aggregate. Revenue figures on this dashboard use net revenue and may change after refunds. Data Platform owns operational data quality; Finance Analytics owns the sales metric definitions.
