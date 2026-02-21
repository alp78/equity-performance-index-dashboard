WITH Filtered AS (
    SELECT symbol, industry, close, trade_date
    FROM {table}
    WHERE sector = ?
      AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
      AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND close IS NOT NULL AND close > 0
),
Ranked AS (
    SELECT symbol, industry,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM Filtered
)
SELECT industry,
    AVG(((last_val - first_val) / NULLIF(first_val, 0)) * 100) as return_pct,
    COUNT(DISTINCT symbol) as stock_count
FROM Ranked WHERE rn = 1
GROUP BY industry HAVING COUNT(DISTINCT symbol) >= 1
ORDER BY return_pct DESC
