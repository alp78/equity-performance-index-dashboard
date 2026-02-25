-- =========================================================================
--  Sector Rankings: Best/Worst Sectors Globally (Lookback Period)
-- =========================================================================
--  Ranks the 11 GICS sectors by average stock return across ALL loaded
--  indices combined.  Used by the SectorRankings component in the sector
--  analysis view to show which sectors are leading or lagging worldwide.
--
--  The {union} placeholder is dynamically replaced with a UNION ALL of
--  every loaded per-index table (prices_sp500 UNION ALL prices_stoxx50 …).
--
--  Placeholders : {union}, {days}
--  Called by    : GET /top-sectors
-- =========================================================================

WITH AllData AS ({union}),
MaxDate AS (SELECT MAX(trade_date) as md FROM AllData),
PerSymbol AS (
    SELECT a.symbol, a.sector,
        ((ARG_MAX(a.close, a.trade_date) - ARG_MIN(a.close, a.trade_date)) / NULLIF(ARG_MIN(a.close, a.trade_date), 0)) * 100 as return_pct
    FROM AllData a, MaxDate m
    WHERE a.trade_date >= m.md - INTERVAL '{days} days'
      AND a.sector IS NOT NULL AND a.sector NOT IN ('N/A', '0', '')
    GROUP BY a.symbol, a.sector
)
SELECT sector, AVG(return_pct) as value, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY sector HAVING COUNT(*) >= 1
ORDER BY value DESC
