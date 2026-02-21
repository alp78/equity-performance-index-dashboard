-- ranks sectors by average stock return across all indices over all available data
WITH AllData AS ({union}),
PerSymbol AS (
    SELECT symbol, sector,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM AllData
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
)
SELECT sector,
    AVG(((last_val - first_val) / NULLIF(first_val, 0)) * 100) as value,
    COUNT(DISTINCT symbol) as stock_count
FROM PerSymbol WHERE rn = 1
GROUP BY sector HAVING COUNT(DISTINCT symbol) >= 2
ORDER BY value DESC
