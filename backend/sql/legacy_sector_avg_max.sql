-- =========================================================================
--  Legacy: Sector Average Close Price (Full History)
-- =========================================================================
--  Computes a simple daily average of raw close prices across all stocks
--  in a sector.  Superseded by clean_sector_series.sql which normalises
--  to percent-change and forward-fills gaps.  Kept for backward compat.
--
--  Placeholders : {table} — per-index DuckDB table
--  Parameters   : ?       — sector name
--  Called by    : (legacy code path — not currently referenced)
-- =========================================================================
WITH sector_stocks AS (
    SELECT symbol,
           CAST(trade_date AS DATE)::VARCHAR as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector = ? AND sector IS NOT NULL
),
daily_avg AS (
    SELECT time, AVG(close) as avg_close
    FROM sector_stocks
    GROUP BY time ORDER BY time ASC
)
SELECT time, avg_close as close FROM daily_avg
