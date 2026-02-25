-- =========================================================================
--  Sidebar: Industries within a Sector
-- =========================================================================
--  Returns the list of industries that belong to a sector along with the
--  number of stocks in each.  Powers the industry dropdown / chip list
--  shown when the user drills into a sector on the SectorHeatmap.
--
--  Placeholders : {table} — per-index DuckDB table
--  Parameters   : ?       — sector name
--  Called by    : GET /sector_industries  →  SectorHeatmap industry picker
-- =========================================================================
SELECT DISTINCT industry, COUNT(DISTINCT symbol) as cnt
FROM {table}
WHERE sector = ?
  AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
GROUP BY industry
