-- =========================================================================
--  Sector Heatmap: Average Return per Sector (Custom Date Range)
-- =========================================================================
--  Same as sector_returns_period.sql but bounded by user-specified dates.
--
--  Placeholders : {table}, {start}, {end}
--  Called by    : GET /sector-table
-- =========================================================================

WITH PerSymbol AS (
    SELECT symbol, sector,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM {table}
    WHERE trade_date >= '{start}' AND trade_date <= '{end}'
      AND sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
    GROUP BY symbol, sector
)
SELECT sector, AVG(return_pct) as return_pct, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY sector HAVING COUNT(*) >= 1
