-- ranks all symbols in an index by return percent over a recent lookback period
WITH Filtered AS (
    SELECT symbol, close, trade_date FROM {table}
    WHERE market_index = '{index}'
      AND trade_date >= (SELECT MAX(trade_date) FROM {table} WHERE market_index = '{index}') - INTERVAL {days} DAY
),
Ranked AS (
    SELECT symbol,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM Filtered
)
SELECT symbol, ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
FROM Ranked WHERE rn = 1
ORDER BY value DESC
