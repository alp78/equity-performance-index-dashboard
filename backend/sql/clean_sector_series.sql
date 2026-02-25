-- =========================================================================
--  On-Demand: Sector/Industry Line-Chart Series (Forward-Filled)
-- =========================================================================
--  Builds a daily average percent-change time series for all stocks that
--  belong to a given sector (and optionally a specific industry).  Each
--  stock is rebased to 0 % at the start of the window, then the daily
--  average across stocks produces a single "sector performance" line.
--
--  Forward-fill (LAST_VALUE IGNORE NULLS) ensures stocks with different
--  trading calendars don't create gaps in the series.
--
--  Placeholders : {table}           — per-index DuckDB table
--                 {sector_clause}   — WHERE sector = ?
--                 {industry_clause} — AND industry = ? (optional)
--                 {date_clause}     — AND trade_date >= ... (optional)
--  Called by    : GET /sector_series  →  SectorHeatmap drill-down chart
-- =========================================================================
WITH
raw AS (
    SELECT symbol,
           CAST(trade_date AS DATE)::VARCHAR as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE {sector_clause}{industry_clause}{date_clause}
      AND close IS NOT NULL AND close > 0
),

bases AS (
    SELECT symbol, ARG_MIN(close, time) as base_close
    FROM raw
    GROUP BY symbol
),

per_stock_pct AS (
    SELECT r.symbol, r.time,
           ((r.close - b.base_close) / b.base_close) * 100 as pct
    FROM raw r
    JOIN bases b ON r.symbol = b.symbol
),

all_dates AS (
    SELECT DISTINCT time FROM per_stock_pct ORDER BY time
),
all_symbols AS (
    SELECT DISTINCT symbol FROM per_stock_pct
),

grid AS (
    SELECT s.symbol, d.time
    FROM all_symbols s CROSS JOIN all_dates d
),

with_gaps AS (
    SELECT g.symbol, g.time, p.pct
    FROM grid g
    LEFT JOIN per_stock_pct p ON g.symbol = p.symbol AND g.time = p.time
),

filled AS (
    SELECT symbol, time,
           LAST_VALUE(pct IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as pct
    FROM with_gaps
),

daily_avg AS (
    SELECT time, AVG(pct) as avg_pct
    FROM filled
    WHERE pct IS NOT NULL
    GROUP BY time
    ORDER BY time ASC
)

SELECT time, avg_pct as pct FROM daily_avg
