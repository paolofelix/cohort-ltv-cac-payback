-- Cohort model: one row per signup cohort (month x channel) x month_number,
-- where month_number 0 is the signup month (first box), 1 is the next month, and so on.
--
-- The grid is built first (GENERATE_ARRAY) so that months where nobody ordered
-- still appear with 0 active customers, and every cohort stops at the last full
-- month of data (Sep 2026). Newer cohorts have fewer months: that is censoring,
-- not churn, and 06_reporting_views.sql only compares cohorts over months they have all reached.

DECLARE data_end_month DATE DEFAULT DATE '2026-09-01';

CREATE OR REPLACE TABLE cohort_ltv.cohort_monthly AS
WITH cohorts AS (
  SELECT cohort_month, channel, COUNT(*) AS cohort_size
  FROM cohort_ltv.stg_customers
  GROUP BY cohort_month, channel
),
grid AS (
  SELECT c.cohort_month, c.channel, c.cohort_size, month_number
  FROM cohorts c,
  UNNEST(GENERATE_ARRAY(0, DATE_DIFF(data_end_month, c.cohort_month, MONTH))) AS month_number
),
activity AS (
  SELECT
    c.cohort_month,
    c.channel,
    DATE_DIFF(o.order_month, c.cohort_month, MONTH) AS month_number,
    COUNT(DISTINCT o.customer_id)                   AS active_customers,
    SUM(o.net_revenue)                              AS net_revenue,
    SUM(o.contribution)                             AS contribution
  FROM cohort_ltv.stg_orders o
  JOIN cohort_ltv.stg_customers c USING (customer_id)
  GROUP BY 1, 2, 3
)
SELECT
  g.cohort_month,
  g.channel,
  g.month_number,
  g.cohort_size,
  COALESCE(a.active_customers, 0)                       AS active_customers,
  ROUND(COALESCE(a.net_revenue, 0), 2)                  AS net_revenue,
  ROUND(COALESCE(a.contribution, 0), 2)                 AS contribution,
  SAFE_DIVIDE(COALESCE(a.active_customers, 0), g.cohort_size) AS retention,
  -- running total per cohort: lifetime contribution so far, per acquired customer
  SUM(COALESCE(a.contribution, 0)) OVER (
    PARTITION BY g.cohort_month, g.channel ORDER BY g.month_number
  ) / g.cohort_size                                     AS cum_contribution_per_customer
FROM grid g
LEFT JOIN activity a
  ON  a.cohort_month = g.cohort_month
  AND a.channel      = g.channel
  AND a.month_number = g.month_number;

-- Sanity check: total contribution in the cohort table should equal total contribution in stg_orders.
-- SELECT (SELECT ROUND(SUM(contribution), 0) FROM cohort_ltv.cohort_monthly) AS cohort_total,
--        (SELECT ROUND(SUM(contribution), 0) FROM cohort_ltv.stg_orders)     AS orders_total;
