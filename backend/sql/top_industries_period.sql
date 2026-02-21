-- ranks industries by average stock return across all indices for a recent lookback period
WITH AllData AS ({union}),
MaxDate AS (SELECT MAX(trade_date) as md FROM AllData),
Filtered AS (
    SELECT a.symbol, a.industry, a.close, a.trade_date
    FROM AllData a, MaxDate m
    WHERE a.trade_date >= m.md - INTERVAL '{days} days'
      AND a.industry IS NOT NULL AND a.industry NOT IN ('N/A', '0', '')
),
PerSymbol AS (
    SELECT symbol, industry,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM Filtered
)
SELECT industry,
    AVG(((last_val - first_val) / NULLIF(first_val, 0)) * 100) as value,
    COUNT(DISTINCT symbol) as stock_count
FROM PerSymbol WHERE rn = 1
GROUP BY industry HAVING COUNT(DISTINCT symbol) >= 2
ORDER BY value DESC
