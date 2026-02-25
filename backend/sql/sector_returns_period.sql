-- =========================================================================
--  Sector Heatmap: Average Return per Sector (Lookback Period)
-- =========================================================================
--  For one index, computes each stock's return over the last N days, then
--  averages by sector.  Powers the sector heatmap grid cells — each cell
--  is one sector's aggregate performance for one index.
--
--  Placeholders : {table} — per-index table, {days} — lookback
--  Called by    : GET /sector-table
-- =========================================================================

WITH PerSymbol AS (
    SELECT symbol, sector,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM {table}
    WHERE trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
    GROUP BY symbol, sector
)
SELECT sector, AVG(return_pct) as return_pct, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY sector HAVING COUNT(*) >= 1
