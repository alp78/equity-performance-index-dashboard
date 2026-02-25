-- =========================================================================
--  Sector Drill-Down: Top/Bottom Stocks in a Sector (Lookback Period)
-- =========================================================================
--  Within a single sector, ranks every stock by price return.  The
--  SectorTopStocks component shows the top 5 and bottom 5 — giving the
--  user a quick view of winners and losers inside a sector.
--
--  Placeholders : {table}, {days}
--  Params       : ? — sector name
--  Called by    : GET /sector-top-stocks
-- =========================================================================

SELECT symbol,
    ARG_MAX(name, trade_date) as name,
    ARG_MAX(industry, trade_date) as industry,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
FROM {table}
WHERE sector = ?
  AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
  AND close IS NOT NULL AND close > 0
GROUP BY symbol
ORDER BY return_pct DESC
