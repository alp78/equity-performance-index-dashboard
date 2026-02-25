-- =========================================================================
--  Industry Rankings: Best/Worst Industries Globally (Full History)
-- =========================================================================
--  Same as top_industries_period.sql but over all available data.
--
--  Placeholders : {union}
--  Called by    : GET /top-industries
-- =========================================================================

WITH AllData AS ({union}),
PerSymbol AS (
    SELECT symbol, industry,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM AllData
    WHERE industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
    GROUP BY symbol, industry
)
SELECT industry, AVG(return_pct) as value, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY industry HAVING COUNT(*) >= 1
ORDER BY value DESC
