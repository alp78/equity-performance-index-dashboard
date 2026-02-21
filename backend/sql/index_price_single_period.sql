-- returns OHLCV data with 30-day and 90-day moving averages for a single index symbol over a recent period
SELECT strftime(trade_date, '%Y-%m-%d') as time, open, close, high, low, volume,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
FROM index_prices
WHERE symbol = ?
  AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
ORDER BY trade_date ASC
