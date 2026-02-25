-- =========================================================================
--  Startup Precompute: Per-Stock Returns (Full History)
-- =========================================================================
--  Same as precompute_stock_returns.sql but covers the entire available
--  history — no date filter.  Populates the "MAX" row in the precomputed
--  rankings table.
--
--  Placeholders : {table}  — per-index DuckDB table
--  Called by    : build_precomputed_returns()  (startup + refresh)
-- =========================================================================
SELECT symbol,
    ARG_MAX(name, trade_date) as name,
    ARG_MAX(industry, trade_date) as industry,
    ARG_MAX(sector, trade_date) as sector,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
FROM {table}
WHERE close IS NOT NULL AND close > 0
GROUP BY symbol
