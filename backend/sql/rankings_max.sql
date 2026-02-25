-- =========================================================================
--  Top Movers: Stock Rankings (Full History)
-- =========================================================================
--  Same as rankings_period.sql but over the entire available history.
--
--  Placeholders : {table} — per-index table, {index} — index key
--  Called by    : GET /rankings
-- =========================================================================

SELECT symbol,
    ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as value
FROM {table}
WHERE market_index = '{index}'
GROUP BY symbol
ORDER BY value DESC
