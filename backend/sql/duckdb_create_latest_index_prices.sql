-- =========================================================================
--  DuckDB Setup: Create Latest-Row Snapshot (Per-Index)
-- =========================================================================
--  Same concept as duckdb_create_latest_table.sql, but for benchmark index
--  symbols.  Keeps one row per index with the most recent close and the
--  previous close, enabling the /index-prices/summary endpoint to show
--  daily change % for each market.
--
--  Called by : _load_index_prices_from_bq()
-- =========================================================================

CREATE TABLE latest_index_prices AS
SELECT symbol, name, currency, exchange, trade_date, open, close, high, low, volume,
    LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price
FROM index_prices
QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
