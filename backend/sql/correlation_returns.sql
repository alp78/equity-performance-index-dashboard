-- =========================================================================
--  Correlation Matrix: Daily Returns for Cross-Index Comparison
-- =========================================================================
--  Computes daily log-style returns (close/prev_close - 1) for every
--  index symbol in index_prices.  The backend pivots these into a
--  correlation matrix so the CorrelationHeatmap can show how closely
--  each pair of indices moves together.
--
--  Placeholders : {date_clause} — AND trade_date >= ... (optional)
--  Called by    : GET /correlation  →  CorrelationHeatmap
-- =========================================================================
WITH prices AS (
    SELECT symbol,
           CAST(trade_date AS DATE)::VARCHAR as time,
           CAST(close AS FLOAT) as close
    FROM index_prices
    WHERE close IS NOT NULL AND close > 0
    {date_clause}
),
daily_returns AS (
    SELECT symbol, time,
           (close / LAG(close) OVER (PARTITION BY symbol ORDER BY time) - 1) as ret
    FROM prices
)
SELECT symbol, time, ret
FROM daily_returns
WHERE ret IS NOT NULL
ORDER BY time, symbol
