-- =========================================================================
--  Industry Breakdown: Return per Industry within a Sector (Lookback)
-- =========================================================================
--  Drills one level deeper than sector_returns — within a selected sector,
--  computes average stock return per industry.  Powers the industry
--  breakdown bar chart shown when the user clicks a sector.
--
--  Placeholders : {table}, {days}
--  Params       : ? — sector name (e.g. 'Information Technology')
--  Called by    : GET /industry-breakdown
-- =========================================================================

WITH PerSymbol AS (
    SELECT symbol, industry,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM {table}
    WHERE sector = ?
      AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
      AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND close IS NOT NULL AND close > 0
    GROUP BY symbol, industry
)
SELECT industry, AVG(return_pct) as return_pct, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY industry HAVING COUNT(*) >= 1
ORDER BY return_pct DESC
