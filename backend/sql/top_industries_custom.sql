-- =========================================================================
--  Industry Rankings: Best/Worst Industries Globally (Custom Date Range)
-- =========================================================================
--  Same as top_industries_period.sql but with user-specified dates.
--
--  Placeholders : {union}, {start}, {end}
--  Called by    : GET /top-industries
-- =========================================================================

WITH AllData AS ({union}),
PerSymbol AS (
    SELECT symbol, industry,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM AllData
    WHERE trade_date >= '{start}' AND trade_date <= '{end}'
      AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
    GROUP BY symbol, industry
)
SELECT industry, AVG(return_pct) as value, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY industry HAVING COUNT(*) >= 1
ORDER BY value DESC
