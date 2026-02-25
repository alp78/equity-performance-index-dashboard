-- =========================================================================
--  DuckDB Setup: Create Latest-Row Snapshot (Per-Stock)
-- =========================================================================
--  For each stock in an index, keeps only the most recent trading day and
--  attaches the previous day's close price.  This powers the /summary
--  endpoint — the sidebar stock list with current price, daily change %,
--  sector, and industry.
--
--  Placeholders : {latest_table} — target (e.g. latest_sp500)
--                 {table_name}   — source (e.g. prices_sp500)
--  Called by    : _load_index_from_bq()
-- =========================================================================

CREATE TABLE {latest_table} AS
SELECT symbol, name, sector, industry, market_index, trade_date, open, close, high, low, volume,
    LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price
FROM {table_name}
QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
