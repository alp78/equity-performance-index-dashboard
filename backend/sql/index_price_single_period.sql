-- =========================================================================
--  Single-Index Chart Data (Lookback Period)
-- =========================================================================
--  Same as index_price_single_max.sql but restricted to a recent lookback
--  window.  Returns OHLCV + 30-day / 90-day moving averages for one index
--  over the last N days.
--
--  Placeholders : {days} — lookback window in days
--  Parameters   : ?      — index symbol (e.g. ^GSPC)
--  Called by    : GET /index_price_single  →  IndexDetailChart (period=7d…1y)
-- =========================================================================
SELECT CAST(trade_date AS DATE)::VARCHAR as time, open, close, high, low, volume,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
FROM index_prices
WHERE symbol = ?
  AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
ORDER BY trade_date ASC
