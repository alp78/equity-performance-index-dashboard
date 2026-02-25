-- =========================================================================
--  Industry Rankings: Best/Worst Industries Globally (Lookback Period)
-- =========================================================================
--  Same approach as top_sectors but at the industry level — ranks every
--  distinct industry across all indices by average stock return.
--  Powers the SectorRankings component's industry tab.
--
--  Placeholders : {union}, {days}
--  Called by    : GET /top-industries
-- =========================================================================

WITH AllData AS ({union}),
MaxDate AS (SELECT MAX(trade_date) as md FROM AllData),
PerSymbol AS (
    SELECT a.symbol, a.industry,
        ((ARG_MAX(a.close, a.trade_date) - ARG_MIN(a.close, a.trade_date)) / NULLIF(ARG_MIN(a.close, a.trade_date), 0)) * 100 as return_pct
    FROM AllData a, MaxDate m
    WHERE a.trade_date >= m.md - INTERVAL '{days} days'
      AND a.industry IS NOT NULL AND a.industry NOT IN ('N/A', '0', '')
    GROUP BY a.symbol, a.industry
)
SELECT industry, AVG(return_pct) as value, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY industry HAVING COUNT(*) >= 1
ORDER BY value DESC
