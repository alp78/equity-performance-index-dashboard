-- =========================================================================
--  Startup Precompute: Sector Time-Series (Forward-Filled)
-- =========================================================================
--  Builds a daily percent-change time series for every GICS sector in an
--  index.  Each stock is normalised to 0 % at its first available date,
--  then forward-filled across any missing trading days.  The daily average
--  across all stocks in a sector becomes that sector's series.
--
--  Run once per index at startup.  The result is stored as a DuckDB table
--  (sector_series_{index}) and pre-loaded into ALL_SERIES_CACHE so the
--  /all-series endpoint serves instantly without a cold query.
--
--  Placeholder : {table} — per-index table (e.g. prices_sp500)
--  Called by   : _precompute_sector_series()
-- =========================================================================

WITH
raw AS (
    SELECT symbol, sector,
           CAST(trade_date AS DATE)::VARCHAR as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
      AND close IS NOT NULL AND close > 0
),

bases AS (
    SELECT symbol, ARG_MIN(close, time) as base_close
    FROM raw
    GROUP BY symbol
),

per_stock_pct AS (
    SELECT r.symbol, r.sector, r.time,
           ((r.close - b.base_close) / b.base_close) * 100 as pct
    FROM raw r
    JOIN bases b ON r.symbol = b.symbol
),

all_dates_per_sector AS (
    SELECT DISTINCT sector, time FROM per_stock_pct
),
all_symbols_per_sector AS (
    SELECT DISTINCT sector, symbol FROM per_stock_pct
),

grid AS (
    SELECT s.sector, s.symbol, d.time
    FROM all_symbols_per_sector s
    JOIN all_dates_per_sector d ON s.sector = d.sector
),

with_gaps AS (
    SELECT g.sector, g.symbol, g.time, p.pct
    FROM grid g
    LEFT JOIN per_stock_pct p ON g.symbol = p.symbol AND g.time = p.time
),

filled AS (
    SELECT sector, symbol, time,
           LAST_VALUE(pct IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as pct
    FROM with_gaps
),

daily_avg AS (
    SELECT sector, time, AVG(pct) as pct
    FROM filled
    WHERE pct IS NOT NULL
    GROUP BY sector, time
    ORDER BY sector, time ASC
)

SELECT sector, time, pct FROM daily_avg
