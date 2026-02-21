SELECT strftime(trade_date, '%Y-%m-%d') as time, open, close, high, low, volume,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
FROM {table}
WHERE symbol = ?
ORDER BY trade_date ASC
