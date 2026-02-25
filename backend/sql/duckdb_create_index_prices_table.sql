-- =========================================================================
--  DuckDB Setup: Create Deduplicated Index-Prices Table
-- =========================================================================
--  Same deduplication logic as duckdb_create_index_table.sql, but for the
--  benchmark index symbols (^GSPC, ^STOXX50E, etc.) rather than individual
--  stocks.  The resulting "index_prices" table drives the macro overview
--  chart and all index-level volatility/return calculations.
--
--  Called by : _load_index_prices_from_bq()
-- =========================================================================

CREATE TABLE index_prices AS
SELECT symbol, name, currency, exchange, trade_date, open, close, high, low, volume
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY symbol, trade_date ORDER BY volume DESC
    ) as rn FROM temp_index_prices
) WHERE rn = 1
