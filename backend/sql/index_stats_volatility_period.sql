-- =========================================================================
--  Index Statistics: Annualized Volatility (Lookback Period)
-- =========================================================================
--  Computes annualized volatility (stddev_daily x sqrt(252)) for one index
--  over a recent lookback window.  Shown in the Macro Overview summary
--  table alongside return and latest price.
--
--  Placeholders : {days} — lookback window in days
--  Parameters   : ?, ?   — index symbol (bound twice: subquery + outer)
--  Called by    : GET /index_prices_summary  →  IndexPerformanceTable
-- =========================================================================
SELECT STDDEV(daily_ret) * SQRT(252) as volatility FROM (
    SELECT (close / LAG(close) OVER (ORDER BY trade_date) - 1) as daily_ret
    FROM index_prices
    WHERE symbol = ? AND trade_date >= (
        SELECT MAX(trade_date) - INTERVAL '{days} days' FROM index_prices WHERE symbol = ?
    )
) WHERE daily_ret IS NOT NULL
