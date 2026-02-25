-- =========================================================================
--  Single-Stock Chart Data (Full History)
-- =========================================================================
--  Returns the complete OHLCV time series for one stock plus its 30-day
--  and 90-day simple moving averages.  Drives the main candlestick chart
--  when the user selects a stock and chooses the "MAX" time period.
--
--  Placeholders : {table} — per-index table (e.g. prices_sp500)
--  Params       : ?  — stock symbol (e.g. NVDA)
--  Called by    : GET /symbol-data
-- =========================================================================

SELECT CAST(trade_date AS DATE)::VARCHAR as time, open, close, high, low, volume,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
FROM {table}
WHERE symbol = ?
ORDER BY trade_date ASC
