-- =========================================================================
--  Legacy: Sector Average Close Price (Lookback Period)
-- =========================================================================
--  Same as legacy_sector_avg_max.sql but restricted to a recent lookback
--  window.  Superseded by clean_sector_series.sql.
--
--  Placeholders : {table} — per-index DuckDB table
--                 {days}  — lookback window in days
--  Parameters   : ?       — sector name
--  Called by    : (legacy code path — not currently referenced)
-- =========================================================================
WITH sector_stocks AS (
    SELECT symbol,
           CAST(trade_date AS DATE)::VARCHAR as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector = ? AND sector IS NOT NULL
      AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
),
daily_avg AS (
    SELECT time, AVG(close) as avg_close
    FROM sector_stocks
    GROUP BY time ORDER BY time ASC
)
SELECT time, avg_close as close FROM daily_avg
