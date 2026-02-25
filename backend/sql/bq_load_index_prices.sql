-- =========================================================================
--  BigQuery → DuckDB: Load Index-Level Price Data
-- =========================================================================
--  Pulls raw OHLCV rows for the benchmark indices themselves (^GSPC,
--  ^STOXX50E, ^FTSE, etc.) — not individual stocks.  Used for the macro
--  overview comparison chart and per-index volatility calculations.
--
--  Placeholder : {table_id}  — fully-qualified BQ table for index prices
--  Called by   : _load_index_prices_from_bq()
-- =========================================================================

SELECT symbol, name, currency, exchange,
    CAST(trade_date AS DATE) as trade_date,
    CAST(open_price AS FLOAT64) as open,
    CAST(close_price AS FLOAT64) as close,
    CAST(high_price AS FLOAT64) as high,
    CAST(low_price AS FLOAT64) as low,
    CAST(volume AS INT64) as volume
FROM `{table_id}`
