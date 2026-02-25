-- =========================================================================
--  Top Movers: Stock Rankings (Custom Date Range)
-- =========================================================================
--  Same as rankings_period.sql but bounded by user-specified start/end
--  dates.  Activated when the user drags a custom range on the chart.
--
--  Placeholders : {table}, {index}, {start}, {end}
--  Called by    : GET /rankings/custom
-- =========================================================================

SELECT symbol,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as value
FROM {table}
WHERE market_index = '{index}'
  AND trade_date >= '{start}' AND trade_date <= '{end}'
GROUP BY symbol
ORDER BY value DESC
