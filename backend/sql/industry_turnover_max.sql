-- =========================================================================
--  Industry Turnover: Trading Volume by Industry (Full History)
-- =========================================================================
--  Same as industry_turnover_period.sql but over all available data.
--
--  Placeholders : {table}
--  Params       : ? — sector name
--  Called by    : GET /industry-turnover
-- =========================================================================

SELECT industry,
    SUM(close * volume) as turnover,
    COUNT(DISTINCT symbol) as stock_count
FROM {table}
WHERE sector = ?
  AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
  AND close IS NOT NULL AND close > 0
  AND volume IS NOT NULL AND volume > 0
GROUP BY industry HAVING COUNT(DISTINCT symbol) >= 1
ORDER BY turnover DESC
