-- =========================================================================
--  Sector Drill-Down: Top/Bottom Stocks in a Sector (Custom Date Range)
-- =========================================================================
--  Same as sector_top_stocks_period.sql but with user-specified dates.
--
--  Placeholders : {table}, {start}, {end}
--  Params       : ? — sector name
--  Called by    : GET /sector-top-stocks
-- =========================================================================

SELECT symbol,
    ARG_MAX(name, trade_date) as name,
    ARG_MAX(industry, trade_date) as industry,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
FROM {table}
WHERE sector = ?
  AND trade_date >= '{start}' AND trade_date <= '{end}'
  AND close IS NOT NULL AND close > 0
GROUP BY symbol
ORDER BY return_pct DESC
