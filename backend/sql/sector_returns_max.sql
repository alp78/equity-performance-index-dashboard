-- =========================================================================
--  Sector Heatmap: Average Return per Sector (Full History)
-- =========================================================================
--  Same as sector_returns_period.sql but over all available data.
--
--  Placeholders : {table} — per-index table
--  Called by    : GET /sector-table
-- =========================================================================

WITH PerSymbol AS (
    SELECT symbol, sector,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM {table}
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
    GROUP BY symbol, sector
)
SELECT sector, AVG(return_pct) as return_pct, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY sector HAVING COUNT(*) >= 1
