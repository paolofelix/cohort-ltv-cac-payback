-- Reporting layer: retention, CAC, LTV and payback.
-- 07_channel_comparison.sql and the Python charts read from these views.
--
-- LTV window: cohorts Oct 2024 - Sep 2025. All of them have 12 full months of orders
-- (month_number 0-11), so every channel is compared over the same horizon.
-- LTV here = cumulative contribution margin per acquired customer, not revenue.

-- 1. Retention triangle, all channels combined (feeds the heatmap).
CREATE OR REPLACE VIEW cohort_ltv.rpt_cohort_retention AS
SELECT
  cohort_month,
  month_number,
  SUM(cohort_size)                                      AS cohort_size,
  SUM(active_customers)                                 AS active_customers,
  ROUND(SAFE_DIVIDE(SUM(active_customers), SUM(cohort_size)), 4) AS retention
FROM cohort_ltv.cohort_monthly
GROUP BY cohort_month, month_number;

-- 2. CAC by channel and signup month.
--    media_cac  = ad platform spend only
--    loaded_cac = media + agency fees + creative production (software like Klaviyo is excluded)
CREATE OR REPLACE VIEW cohort_ltv.rpt_channel_cac AS
WITH spend AS (
  SELECT
    month, channel,
    SUM(IF(category = 'Media', amount, 0)) AS media_spend,
    SUM(amount)                            AS loaded_spend
  FROM cohort_ltv.stg_marketing_spend
  WHERE channel != 'Non-acquisition'
  GROUP BY month, channel
),
new_customers AS (
  SELECT cohort_month AS month, channel, COUNT(*) AS new_customers
  FROM cohort_ltv.stg_customers
  WHERE channel IN ('Google Ads', 'Meta Ads', 'TikTok Ads')
  GROUP BY 1, 2
)
SELECT
  month,
  channel,
  n.new_customers,
  ROUND(s.media_spend, 2)                                AS media_spend,
  ROUND(s.loaded_spend, 2)                               AS loaded_spend,
  ROUND(SAFE_DIVIDE(s.media_spend, n.new_customers), 2)  AS media_cac,
  ROUND(SAFE_DIVIDE(s.loaded_spend, n.new_customers), 2) AS loaded_cac
FROM new_customers n
FULL JOIN spend s USING (month, channel);

-- 3. LTV curve by channel: retention and cumulative contribution per customer, months 0-11.
CREATE OR REPLACE VIEW cohort_ltv.rpt_channel_ltv_curve AS
WITH ltv_cohorts AS (
  SELECT *
  FROM cohort_ltv.cohort_monthly
  WHERE cohort_month BETWEEN DATE '2024-10-01' AND DATE '2025-09-01'
    AND month_number <= 11
),
by_month AS (
  SELECT
    channel,
    month_number,
    SUM(cohort_size)      AS customers,
    SUM(active_customers) AS active_customers,
    SUM(contribution)     AS contribution
  FROM ltv_cohorts
  GROUP BY channel, month_number
)
SELECT
  channel,
  month_number,
  customers,
  active_customers,
  ROUND(active_customers / customers, 4)                               AS retention,
  ROUND(SUM(contribution) OVER (PARTITION BY channel ORDER BY month_number)
        / customers, 2)                                                AS cum_contribution_per_customer
FROM by_month;

-- 4. One row per channel: retention, LTV at 3 / 6 / 12 months, CAC, LTV:CAC and payback month.
--    payback_month = number of monthly orders needed before cumulative contribution covers loaded CAC
--    (NULL = not paid back within 12 months).
CREATE OR REPLACE VIEW cohort_ltv.rpt_channel_summary AS
WITH curve AS (
  SELECT
    channel,
    MAX(customers)                                             AS customers,
    MAX(IF(month_number = 1,  retention, NULL))                AS m1_retention,
    MAX(IF(month_number = 6,  retention, NULL))                AS m6_retention,
    MAX(IF(month_number = 2,  cum_contribution_per_customer, NULL)) AS ltv_3m,
    MAX(IF(month_number = 5,  cum_contribution_per_customer, NULL)) AS ltv_6m,
    MAX(IF(month_number = 11, cum_contribution_per_customer, NULL)) AS ltv_12m
  FROM cohort_ltv.rpt_channel_ltv_curve
  GROUP BY channel
),
cac AS (
  SELECT
    channel,
    SAFE_DIVIDE(SUM(media_spend),  SUM(new_customers)) AS media_cac,
    SAFE_DIVIDE(SUM(loaded_spend), SUM(new_customers)) AS loaded_cac
  FROM cohort_ltv.rpt_channel_cac
  WHERE month BETWEEN DATE '2024-10-01' AND DATE '2025-09-01'
  GROUP BY channel
),
payback AS (
  SELECT l.channel, MIN(l.month_number) + 1 AS payback_month
  FROM cohort_ltv.rpt_channel_ltv_curve l
  JOIN cac c USING (channel)
  WHERE l.cum_contribution_per_customer >= c.loaded_cac
  GROUP BY l.channel
)
SELECT
  channel,
  customers,
  m1_retention,
  m6_retention,
  ltv_3m,
  ltv_6m,
  ltv_12m,
  ROUND(c.media_cac, 2)                        AS media_cac,
  ROUND(c.loaded_cac, 2)                       AS loaded_cac,
  ROUND(SAFE_DIVIDE(ltv_12m, c.loaded_cac), 2) AS ltv_cac_12m,
  p.payback_month
FROM curve
LEFT JOIN cac c USING (channel)
LEFT JOIN payback p USING (channel);
