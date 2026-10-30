-- Data quality check after staging.
-- Expected: customers 3,310 raw rows -> 3,262 unique customers,
-- orders 17,527 raw rows -> 16,974 valid orders (test, duplicate and cancelled rows removed),
-- marketing_spend 163 -> 163, and every "bad_" column = 0.

SELECT 'customers' AS source,
       (SELECT COUNT(*) FROM cohort_ltv.raw_customers) AS raw_rows,
       COUNT(*)                                        AS clean_rows,
       COUNTIF(signup_date IS NULL)                    AS bad_dates,
       COUNTIF(channel = 'Other')                      AS bad_mapping
FROM cohort_ltv.stg_customers
UNION ALL
SELECT 'orders',
       (SELECT COUNT(*) FROM cohort_ltv.raw_orders),
       COUNT(*),
       COUNTIF(o.order_date IS NULL),
       COUNTIF(o.box_cost IS NULL OR o.net_revenue IS NULL
               OR c.customer_id IS NULL)       -- unknown plan, bad amount or orphan order
FROM cohort_ltv.stg_orders o
LEFT JOIN cohort_ltv.stg_customers c USING (customer_id)
UNION ALL
SELECT 'marketing_spend',
       (SELECT COUNT(*) FROM cohort_ltv.raw_marketing_spend),
       COUNT(*),
       COUNTIF(month IS NULL),
       COUNTIF(amount IS NULL)
FROM cohort_ltv.stg_marketing_spend;
