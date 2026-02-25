-- =========================================================================
--  Index Statistics: Annualized Volatility (Full History)
-- =========================================================================
--  Same as index_stats_volatility_period.sql but covers the entire
--  available history — no date filter.
--
--  Parameters : ? — index symbol
--  Called by  : GET /index_prices_summary  →  IndexPerformanceTable (MAX)
-- =========================================================================
SELECT STDDEV(daily_ret) * SQRT(252) as volatility FROM (
    SELECT (close / LAG(close) OVER (ORDER BY trade_date) - 1) as daily_ret
    FROM index_prices
    WHERE symbol = ?
) WHERE daily_ret IS NOT NULL
