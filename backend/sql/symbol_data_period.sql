-- =========================================================================
--  Single-Stock Chart Data (Lookback Period)
-- =========================================================================
--  Same as symbol_data_max.sql but limited to the last N days.
--  Used when the user picks 1W, 1M, 3M, 6M, 1Y, or 5Y on the chart.
--
--  Placeholders : {table} — per-index table, {days} — lookback in days
--  Params       : ? — stock symbol (bound twice: filter + subquery)
--  Called by    : GET /symbol-data
-- =========================================================================

SELECT CAST(trade_date AS DATE)::VARCHAR as time, open, close, high, low, volume,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
FROM {table}
WHERE symbol = ?
  AND trade_date >= (SELECT MAX(trade_date) FROM {table} WHERE symbol = ?) - INTERVAL {days} DAY
ORDER BY trade_date ASC
