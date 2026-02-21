-- returns top stocks by return within a sector for a recent lookback period
WITH Filtered AS (
    SELECT symbol, name, close, trade_date
    FROM {table}
    WHERE sector = ?
      AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
      AND close IS NOT NULL AND close > 0
),
Ranked AS (
    SELECT symbol, name,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM Filtered
)
SELECT symbol, MAX(name) as name,
    ((MAX(last_val) - MAX(first_val)) / NULLIF(MAX(first_val), 0)) * 100 as return_pct
FROM Ranked WHERE rn = 1
GROUP BY symbol
ORDER BY return_pct DESC
