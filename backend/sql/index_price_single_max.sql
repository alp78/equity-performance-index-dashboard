-- =========================================================================
--  Single-Index Chart Data (Full History)
-- =========================================================================
--  Returns the complete OHLCV history for one index symbol with computed
--  30-day and 90-day simple moving averages.  Powers the candlestick /
--  line chart when the user clicks an index row in the Macro Overview
--  and selects the "MAX" time range.
--
--  Parameters : ? — index symbol (e.g. ^GSPC)
--  Called by  : GET /index_price_single  →  IndexDetailChart (period=max)
-- =========================================================================
SELECT CAST(trade_date AS DATE)::VARCHAR as time, open, close, high, low, volume,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
FROM index_prices
WHERE symbol = ?
ORDER BY trade_date ASC
