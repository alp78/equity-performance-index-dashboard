-- ranks sectors by average stock return across all indices for a custom date range
WITH AllData AS ({union}),
Filtered AS (
    SELECT symbol, sector, close, trade_date
    FROM AllData
    WHERE trade_date >= '{start}' AND trade_date <= '{end}'
      AND sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
),
PerSymbol AS (
    SELECT symbol, sector,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM Filtered
)
SELECT sector,
    AVG(((last_val - first_val) / NULLIF(first_val, 0)) * 100) as value,
    COUNT(DISTINCT symbol) as stock_count
FROM PerSymbol WHERE rn = 1
GROUP BY sector HAVING COUNT(DISTINCT symbol) >= 1
ORDER BY value DESC
