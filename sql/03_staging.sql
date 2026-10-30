-- Staging layer: clean and standardize the raw sources.
-- Uses the functions from 02_udfs.sql.

-- One row per customer. The CRM export repeats some customers with a different
-- ID format (c00123 / " C00123"); keep one row each, earliest signup first.
CREATE OR REPLACE VIEW cohort_ltv.stg_customers AS
SELECT
  customer_id,
  signup_date,
  DATE_TRUNC(signup_date, MONTH) AS cohort_month,
  channel,
  utm_campaign
FROM (
  SELECT
    UPPER(TRIM(customer_id))                         AS customer_id,
    cohort_ltv.to_date(signup_date)                  AS signup_date,
    cohort_ltv.map_channel(utm_source, utm_medium)   AS channel,
    LOWER(TRIM(utm_campaign))                        AS utm_campaign
  FROM cohort_ltv.raw_customers
  WHERE TRIM(customer_id) != ''
)
WHERE TRUE
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY signup_date) = 1;

-- Plan costs used for contribution margin.
CREATE OR REPLACE VIEW cohort_ltv.stg_plans AS
SELECT
  UPPER(TRIM(plan_id))                  AS plan_id,
  TRIM(plan_name)                       AS plan_name,
  cohort_ltv.to_num(list_price)         AS list_price,
  cohort_ltv.to_num(cogs_per_box)       AS cogs_per_box,
  cohort_ltv.to_num(shipping_per_box)   AS shipping_per_box
FROM cohort_ltv.raw_plans
WHERE TRIM(plan_id) != '';

-- Valid orders with net revenue and contribution margin.
--   * drops internal TEST customers, duplicate order rows and cancelled (failed payment) orders
--   * refunded orders keep their box cost but earn no revenue
--   * contribution = net revenue - COGS - shipping - 3% payment processing
CREATE OR REPLACE VIEW cohort_ltv.stg_orders AS
WITH cleaned AS (
  SELECT
    TRIM(order_id)                          AS order_id,
    UPPER(TRIM(customer_id))                AS customer_id,
    cohort_ltv.to_date(order_date)          AS order_date,
    UPPER(TRIM(plan_id))                    AS plan_id,
    cohort_ltv.to_num(gross_amount)         AS gross_amount,
    COALESCE(cohort_ltv.to_num(discount), 0) AS discount,
    CASE
      WHEN STARTS_WITH(LOWER(TRIM(status)), 'complete') THEN 'completed'
      WHEN STARTS_WITH(LOWER(TRIM(status)), 'refund')   THEN 'refunded'
      WHEN STARTS_WITH(LOWER(TRIM(status)), 'cancel')   THEN 'cancelled'
      ELSE 'unknown'
    END                                     AS status
  FROM cohort_ltv.raw_orders
  WHERE TRIM(order_id) != ''
),
deduped AS (
  SELECT * FROM cleaned
  WHERE NOT STARTS_WITH(customer_id, 'TEST')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_date) = 1
)
SELECT
  o.order_id,
  o.customer_id,
  o.order_date,
  DATE_TRUNC(o.order_date, MONTH)                         AS order_month,
  o.plan_id,
  o.status,
  o.gross_amount,
  o.discount,
  IF(o.status = 'refunded', 0, o.gross_amount - o.discount) AS net_revenue,
  p.cogs_per_box + p.shipping_per_box                       AS box_cost,
  IF(o.status = 'refunded', 0, o.gross_amount - o.discount) * 0.97
    - p.cogs_per_box - p.shipping_per_box                   AS contribution
FROM deduped o
LEFT JOIN cohort_ltv.stg_plans p USING (plan_id)
WHERE o.status != 'cancelled';

-- Monthly marketing ledger. Maps the messy channel labels and keeps the cost category,
-- so CAC can be calculated on media only or fully loaded (media + agency fee + creative).
CREATE OR REPLACE VIEW cohort_ltv.stg_marketing_spend AS
SELECT
  cohort_ltv.to_month(month)  AS month,
  CASE
    WHEN REGEXP_CONTAINS(LOWER(channel), r'google')         THEN 'Google Ads'
    WHEN REGEXP_CONTAINS(LOWER(channel), r'meta|facebook')  THEN 'Meta Ads'
    WHEN REGEXP_CONTAINS(LOWER(channel), r'tiktok')         THEN 'TikTok Ads'
    ELSE 'Non-acquisition'
  END                         AS channel,
  TRIM(vendor)                AS vendor,
  TRIM(category)              AS category,
  cohort_ltv.to_num(amount)   AS amount
FROM cohort_ltv.raw_marketing_spend
WHERE TRIM(month) != '';
