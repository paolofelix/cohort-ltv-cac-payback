-- Reusable cleaning functions used by the staging views (03_staging.sql).

-- Text -> number. Strips spaces, thousands separators and currency symbols. Keeps the minus sign.
CREATE OR REPLACE FUNCTION cohort_ltv.to_num(s STRING) AS (
  SAFE_CAST(REGEXP_REPLACE(TRIM(s), r'[,$\s]', '') AS FLOAT64)
);

-- Text -> DATE. Accepts 2025-03-04, 03/04/2025, 3/4/2025 and 2025-03-04T14:22:00Z.
CREATE OR REPLACE FUNCTION cohort_ltv.to_date(s STRING) AS (
  COALESCE(
    SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(TRIM(s), 1, 10)),
    SAFE.PARSE_DATE('%m/%d/%Y', TRIM(s))
  )
);

-- Ledger month -> first day of the month. Accepts 2025-03, Mar-2025 and 3/1/2025.
CREATE OR REPLACE FUNCTION cohort_ltv.to_month(s STRING) AS (
  DATE_TRUNC(COALESCE(
    SAFE.PARSE_DATE('%Y-%m-%d', CONCAT(TRIM(s), '-01')),
    SAFE.PARSE_DATE('%d-%b-%Y', CONCAT('01-', TRIM(s))),
    SAFE.PARSE_DATE('%m/%d/%Y', TRIM(s))
  ), MONTH)
);

-- utm_source + utm_medium -> acquisition channel.
-- Medium is checked first, so google / organic lands in Organic, not Google Ads.
CREATE OR REPLACE FUNCTION cohort_ltv.map_channel(source STRING, medium STRING) AS (
  CASE
    WHEN COALESCE(TRIM(LOWER(source)), '') IN ('', '(direct)')
      OR TRIM(LOWER(medium)) IN ('organic', 'email', '(none)')              THEN 'Organic / Direct'
    WHEN REGEXP_CONTAINS(LOWER(medium), r'referral')
      OR REGEXP_CONTAINS(LOWER(source), r'refer|friendbuy')                  THEN 'Referral'
    WHEN TRIM(LOWER(source)) IN ('google', 'adwords')                        THEN 'Google Ads'
    WHEN TRIM(LOWER(source)) IN ('facebook', 'fb', 'ig', 'instagram', 'meta') THEN 'Meta Ads'
    WHEN TRIM(LOWER(source)) IN ('tiktok', 'tt')                             THEN 'TikTok Ads'
    ELSE 'Other'
  END
);
