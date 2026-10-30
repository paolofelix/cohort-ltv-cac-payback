-- Step 0: create the dataset
CREATE SCHEMA IF NOT EXISTS cohort_ltv OPTIONS (location = 'US');

-- Step 1: link each Google Sheet tab as an external table.
-- Replace YOUR_SHEET_ID with the ID from your Google Sheet URL before running.
-- All columns load as STRING; cleaning happens in 03_staging.sql.

CREATE OR REPLACE EXTERNAL TABLE cohort_ltv.raw_customers (
  customer_id STRING, signup_date STRING, utm_source STRING, utm_medium STRING,
  utm_campaign STRING, plan STRING
)
OPTIONS (format = 'GOOGLE_SHEETS', uris = ['https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID'],
  sheet_range = 'customers!A:F', skip_leading_rows = 1);

CREATE OR REPLACE EXTERNAL TABLE cohort_ltv.raw_orders (
  order_id STRING, customer_id STRING, order_date STRING, plan_id STRING,
  gross_amount STRING, discount STRING, status STRING
)
OPTIONS (format = 'GOOGLE_SHEETS', uris = ['https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID'],
  sheet_range = 'orders!A:G', skip_leading_rows = 1);

CREATE OR REPLACE EXTERNAL TABLE cohort_ltv.raw_marketing_spend (
  month STRING, channel STRING, vendor STRING, category STRING, amount STRING
)
OPTIONS (format = 'GOOGLE_SHEETS', uris = ['https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID'],
  sheet_range = 'marketing_spend!A:E', skip_leading_rows = 1);

CREATE OR REPLACE EXTERNAL TABLE cohort_ltv.raw_plans (
  plan_id STRING, plan_name STRING, list_price STRING, cogs_per_box STRING, shipping_per_box STRING
)
OPTIONS (format = 'GOOGLE_SHEETS', uris = ['https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID'],
  sheet_range = 'plans!A:E', skip_leading_rows = 1);
