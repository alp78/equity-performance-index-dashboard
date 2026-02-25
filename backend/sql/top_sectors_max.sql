-- =========================================================================
--  Sector Rankings: Best/Worst Sectors Globally (Full History)
-- =========================================================================
--  Same as top_sectors_period.sql but over all available data.
--
--  Placeholders : {union}
--  Called by    : GET /top-sectors
-- =========================================================================

WITH AllData AS ({union}),
PerSymbol AS (
    SELECT symbol, sector,
        ((ARG_MAX(close, trade_date) - ARG_MIN(close, trade_date)) / NULLIF(ARG_MIN(close, trade_date), 0)) * 100 as return_pct
    FROM AllData
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
    GROUP BY symbol, sector
)
SELECT sector, AVG(return_pct) as value, COUNT(*) as stock_count
FROM PerSymbol
GROUP BY sector HAVING COUNT(*) >= 1
ORDER BY value DESC
