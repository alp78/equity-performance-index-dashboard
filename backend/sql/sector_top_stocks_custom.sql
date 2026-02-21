-- returns top stocks by return within a sector for a custom date range
WITH Filtered AS (
    SELECT symbol, name, sector, close, trade_date
    FROM {table}
    WHERE sector = ?
      AND trade_date >= '{start}' AND trade_date <= '{end}'
      AND close IS NOT NULL AND close > 0
),
Ranked AS (
    SELECT symbol, name, sector,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM Filtered
)
SELECT symbol, MAX(name) as name,
    ((MAX(last_val) - MAX(first_val)) / NULLIF(MAX(first_val), 0)) * 100 as return_pct
FROM Ranked WHERE rn = 1
GROUP BY symbol HAVING COUNT(*) >= 1
ORDER BY return_pct DESC
