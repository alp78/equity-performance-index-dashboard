-- =========================================================================
--  Index Statistics: Period Start Price
-- =========================================================================
--  Fetches the earliest close price for an index within a lookback window.
--  Used to compute the period return (latest close / start close - 1)
--  shown in the Macro Overview summary table.
--
--  Placeholders : {days} — lookback window in days
--  Parameters   : ?, ?   — index symbol (bound twice: subquery + outer)
--  Called by    : GET /index_prices_summary  →  IndexPerformanceTable
-- =========================================================================
SELECT close FROM index_prices
WHERE symbol = ? AND trade_date >= (
    SELECT MAX(trade_date) - INTERVAL '{days} days' FROM index_prices WHERE symbol = ?
)
ORDER BY trade_date ASC LIMIT 1
