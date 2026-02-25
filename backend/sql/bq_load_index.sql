-- =========================================================================
--  BigQuery → DuckDB: Load Stock Index Data
-- =========================================================================
--  Pulls raw OHLCV rows for every stock in a single market index
--  (e.g. S&P 500, STOXX 50) from BigQuery into local memory.
--  This is the first step of the cold-start pipeline — the returned
--  DataFrame is registered as a temp table, then deduplicated by
--  duckdb_create_index_table.sql.
--
--  Placeholder : {table_id}  — fully-qualified BQ table
--  Called by   : _load_index_from_bq()
-- =========================================================================

SELECT symbol, name, sector, industry,
    CAST(trade_date AS DATE) as trade_date,
    CAST(open_price AS FLOAT64) as open,
    CAST(close_price AS FLOAT64) as close,
    CAST(high_price AS FLOAT64) as high,
    CAST(low_price AS FLOAT64) as low,
    CAST(volume AS INT64) as volume
FROM `{table_id}`
