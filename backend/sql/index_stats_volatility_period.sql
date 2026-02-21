SELECT STDDEV(daily_ret) * SQRT(252) as volatility FROM (
    SELECT (close / LAG(close) OVER (ORDER BY trade_date) - 1) as daily_ret
    FROM index_prices
    WHERE symbol = ? AND trade_date >= (
        SELECT MAX(trade_date) - INTERVAL '{days} days' FROM index_prices WHERE symbol = ?
    )
) WHERE daily_ret IS NOT NULL
