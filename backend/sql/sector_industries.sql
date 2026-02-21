-- lists distinct industries and their stock counts within a given sector
SELECT DISTINCT industry, COUNT(DISTINCT symbol) as cnt
FROM {table}
WHERE sector = ?
  AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
GROUP BY industry
