-- Three result tables used in the write-up. Run each query on its own.

-- A. Channel comparison: what each channel costs vs what its customers are worth after 12 months.
SELECT *
FROM cohort_ltv.rpt_channel_summary
ORDER BY loaded_cac DESC NULLS LAST, ltv_12m DESC;


-- B. Budget shift: first 6 months of data vs last 6 months.
-- Expected 12-month LTV weights each channel's mature LTV (from A) by its share of paid signups,
-- because the newest cohorts are too young to measure 12-month LTV directly.
WITH periods AS (
  SELECT '1. Oct 2024 - Mar 2025' AS period, DATE '2024-10-01' AS start_month, DATE '2025-03-01' AS end_month
  UNION ALL
  SELECT '2. Apr 2026 - Sep 2026', DATE '2026-04-01', DATE '2026-09-01'
),
mix AS (
  SELECT p.period, c.channel, SUM(c.new_customers) AS new_customers, SUM(c.loaded_spend) AS loaded_spend
  FROM periods p
  JOIN cohort_ltv.rpt_channel_cac c ON c.month BETWEEN p.start_month AND p.end_month
  GROUP BY 1, 2
)
SELECT
  period,
  ROUND(SUM(IF(m.channel = 'TikTok Ads', new_customers, 0)) / SUM(new_customers), 2) AS tiktok_share_of_paid_signups,
  ROUND(SUM(loaded_spend) / SUM(new_customers), 2)                                    AS blended_loaded_cac,
  ROUND(SUM(new_customers * s.ltv_12m) / SUM(new_customers), 2)                       AS expected_ltv_12m,
  ROUND(SUM(new_customers * s.ltv_12m) / SUM(loaded_spend), 2)                        AS expected_ltv_cac
FROM mix m
JOIN cohort_ltv.rpt_channel_summary s USING (channel)
GROUP BY period
ORDER BY period;


-- C. Black Friday cohorts (November, 50% off the first box) vs all other cohorts
-- with at least 6 months of data (signed up by Mar 2026).
SELECT
  IF(EXTRACT(MONTH FROM cohort_month) = 11, 'Black Friday (Nov)', 'Other months') AS cohort_type,
  SUM(IF(month_number = 0, cohort_size, 0))                                         AS customers,
  ROUND(SUM(IF(month_number = 1, active_customers, 0)) / SUM(IF(month_number = 0, cohort_size, 0)), 2) AS m1_retention,
  ROUND(SUM(IF(month_number = 6, active_customers, 0)) / SUM(IF(month_number = 0, cohort_size, 0)), 2) AS m6_retention,
  ROUND(SUM(IF(month_number <= 5, contribution, 0)) / SUM(IF(month_number = 0, cohort_size, 0)), 2)    AS ltv_6m
FROM cohort_ltv.cohort_monthly
WHERE cohort_month <= DATE '2026-03-01'
GROUP BY cohort_type
ORDER BY cohort_type;
