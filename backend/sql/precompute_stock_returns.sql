-- =========================================================================
--  Startup Precompute: Per-Stock Returns (Lookback Period)
-- =========================================================================
--  Computes total return % for every stock in the index over a recent
--  lookback window.  Runs once at startup (and on refresh) to populate
--  the precomputed rankings table — avoids re-scanning raw trades on
--  every /rankings or /sector_top_stocks request.
--
--  Placeholders : {table}  — per-index DuckDB table
--                 {days}   — lookback window in days
--  Called by    : build_precomputed_returns()  (startup + refresh)
-- =========================================================================
SELECT symbol,
    ARG_MAX(name, trade_date) as name,
    ARG_MAX(industry, trade_date) as industry,
    ARG_MAX(sector, trade_date) as sector,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
FROM {table}
WHERE trade_date >= CURRENT_DATE - INTERVAL '{days} days'
  AND close IS NOT NULL AND close > 0
GROUP BY symbol
