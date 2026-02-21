-- computes annualized volatility for an index symbol over a custom date range
SELECT STDDEV(daily_ret) * SQRT(252) as volatility FROM (
    SELECT (close / LAG(close) OVER (ORDER BY trade_date) - 1) as daily_ret
    FROM index_prices
    WHERE symbol = ? AND trade_date >= '{start}' AND trade_date <= '{end}'
) WHERE daily_ret IS NOT NULL
