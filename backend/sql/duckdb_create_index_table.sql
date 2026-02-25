-- =========================================================================
--  DuckDB Setup: Create Per-Index Stock Table
-- =========================================================================
--  Transforms the raw BigQuery staging data into the final per-index table
--  (e.g. prices_sp500).  Two key operations happen here:
--
--  1. Deduplication — if BigQuery has duplicate rows for the same symbol +
--     date, keep only the row with the highest volume.
--  2. Sector remapping — Yahoo Finance uses non-standard sector names
--     (e.g. "Basic Materials"); we normalise them to GICS standard names
--     so all six indices share a uniform sector taxonomy.
--
--  Placeholders : {table_name} — target table (e.g. prices_sp500)
--                 {index_key}  — index identifier (e.g. sp500)
--  Called by    : _load_index_from_bq()
-- =========================================================================

CREATE TABLE {table_name} AS
SELECT symbol, name,
    CASE sector
        WHEN 'Basic Materials' THEN 'Materials'
        WHEN 'Consumer Cyclical' THEN 'Consumer Discretionary'
        WHEN 'Consumer Defensive' THEN 'Consumer Staples'
        WHEN 'Financial Services' THEN 'Financials'
        WHEN 'Technology' THEN 'Information Technology'
        ELSE sector
    END AS sector,
    industry, trade_date, open, close, high, low, volume, market_index
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY symbol, trade_date ORDER BY volume DESC
    ) as rn FROM temp_{index_key}
) WHERE rn = 1
