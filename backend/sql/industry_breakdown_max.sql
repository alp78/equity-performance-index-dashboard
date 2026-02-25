-- =========================================================================
--  Industry Breakdown: Return per Industry within a Sector (Full History)
-- =========================================================================
--  Same as industry_breakdown_period.sql but over all available data.
--
--  Placeholders : {table}
--  Params       : ? — sector name
--  Called by    : GET /industry-breakdown
-- =========================================================================

WITH PerSymbol AS (
    SELECT symbol, industry,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM {table}
    WHERE sector = ?
      AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
      AND close IS NOT NULL AND close > 0
    GROUP BY symbol, industry
)
SELECT industry, AVG(return_pct) as return_pct, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY industry HAVING COUNT(*) >= 1
ORDER BY return_pct DESC
