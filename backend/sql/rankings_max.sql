-- ranks all symbols in an index by return percent over all available data
WITH Ranked AS (
    SELECT symbol,
        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as start_price,
        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as end_price,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM {table}
    WHERE market_index = '{index}'
)
SELECT symbol, ((end_price - start_price) / NULLIF(start_price, 0)) * 100 as value
FROM Ranked WHERE rn = 1
ORDER BY value DESC
