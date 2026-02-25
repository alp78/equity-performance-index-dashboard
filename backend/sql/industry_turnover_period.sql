-- =========================================================================
--  Industry Turnover: Trading Volume by Industry (Lookback Period)
-- =========================================================================
--  Within a selected sector, totals the dollar turnover (close × volume)
--  per industry.  Used by IndustryBreakdown.svelte to show which
--  industries have the most trading activity — a proxy for investor
--  interest or liquidity.
--
--  Placeholders : {table}, {days}
--  Params       : ? — sector name
--  Called by    : GET /industry-turnover
-- =========================================================================

SELECT industry,
    SUM(close * volume) as turnover,
    COUNT(DISTINCT symbol) as stock_count
FROM {table}
WHERE sector = ?
  AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
  AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
  AND close IS NOT NULL AND close > 0
  AND volume IS NOT NULL AND volume > 0
GROUP BY industry HAVING COUNT(DISTINCT symbol) >= 1
ORDER BY turnover DESC
