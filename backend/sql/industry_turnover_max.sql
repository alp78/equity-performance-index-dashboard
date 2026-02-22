-- computes total turnover (close * volume) per industry within a sector over all available data
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
