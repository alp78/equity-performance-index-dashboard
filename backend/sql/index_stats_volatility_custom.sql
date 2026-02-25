-- =========================================================================
--  Index Statistics: Annualized Volatility (Custom Date Range)
-- =========================================================================
--  Same as index_stats_volatility_period.sql but bounded by explicit
--  start/end dates instead of a rolling lookback.
--
--  Placeholders : {start}, {end} — ISO date strings
--  Parameters   : ?              — index symbol
--  Called by    : GET /index_prices_summary  →  IndexPerformanceTable (custom)
-- =========================================================================
SELECT STDDEV(daily_ret) * SQRT(252) as volatility FROM (
    SELECT (close / LAG(close) OVER (ORDER BY trade_date) - 1) as daily_ret
    FROM index_prices
    WHERE symbol = ? AND trade_date >= '{start}' AND trade_date <= '{end}'
) WHERE daily_ret IS NOT NULL
