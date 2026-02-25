-- =========================================================================
--  Top Movers: Stock Rankings (Lookback Period)
-- =========================================================================
--  Ranks every stock in an index by price return % over the last N days.
--  The frontend takes the top 3 and bottom 3 to display in the "Top Movers"
--  panel (RankingPanel.svelte).
--
--  Placeholders : {table} — per-index table, {index} — index key,
--                 {days}  — lookback in days
--  Called by    : GET /rankings
-- =========================================================================

SELECT symbol,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as value
FROM {table}
WHERE market_index = '{index}'
  AND trade_date >= (SELECT MAX(trade_date) FROM {table} WHERE market_index = '{index}') - INTERVAL {days} DAY
GROUP BY symbol
ORDER BY value DESC
