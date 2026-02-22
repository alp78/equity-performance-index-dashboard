-- ranks industries by average stock return across all indices over all available data
WITH AllData AS ({union}),
PerSymbol AS (
    SELECT symbol, industry,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM AllData
    WHERE industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
)
SELECT industry,
    AVG(((last_val - first_val) / NULLIF(first_val, 0)) * 100) as value,
    COUNT(DISTINCT symbol) as stock_count
FROM PerSymbol WHERE rn = 1
GROUP BY industry HAVING COUNT(DISTINCT symbol) >= 1
ORDER BY value DESC
