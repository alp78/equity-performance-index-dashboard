-- =========================================================================
--  Macro Overview: Multi-Index Comparison Chart
-- =========================================================================
--  Builds a normalized percent-change time series for one or more index
--  symbols (e.g. ^GSPC, ^STOXX50E) so they can be overlaid on the same
--  chart regardless of absolute price level.  Each index is rebased to
--  0 % at the earliest date in the window.
--
--  A date-aligned grid + forward-fill (LAST_VALUE IGNORE NULLS) ensures
--  indices with different trading calendars are comparable day-by-day.
--
--  Placeholders : {placeholders} — comma-separated ? marks for symbols
--                 {date_clause}  — AND trade_date >= ... (optional)
--  Called by    : GET /index_prices_data  →  IndexComparisonChart
-- =========================================================================
WITH
raw AS (
    SELECT symbol,
           CAST(trade_date AS DATE)::VARCHAR as time,
           CAST(close AS FLOAT) as close,
           CAST(volume AS BIGINT) as volume
    FROM index_prices
    WHERE symbol IN ({placeholders}) {date_clause}
      AND close IS NOT NULL AND close > 0
),

bases AS (
    SELECT symbol, ARG_MIN(close, time) as base_close
    FROM raw
    GROUP BY symbol
),

per_sym AS (
    SELECT r.symbol, r.time, r.close, r.volume,
           ((r.close - b.base_close) / b.base_close) * 100 as pct
    FROM raw r
    JOIN bases b ON r.symbol = b.symbol
),

all_dates AS (
    SELECT DISTINCT time FROM per_sym ORDER BY time
),
all_symbols AS (
    SELECT DISTINCT symbol FROM per_sym
),

grid AS (
    SELECT s.symbol, d.time
    FROM all_symbols s CROSS JOIN all_dates d
),

with_gaps AS (
    SELECT g.symbol, g.time, p.close, p.volume, p.pct
    FROM grid g
    LEFT JOIN per_sym p ON g.symbol = p.symbol AND g.time = p.time
),

filled AS (
    SELECT symbol, time,
           LAST_VALUE(close IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as close,
           LAST_VALUE(pct IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as pct,
           COALESCE(volume, 0) as volume
    FROM with_gaps
)

SELECT symbol, time, close, pct, volume
FROM filled
WHERE close IS NOT NULL
ORDER BY symbol, time ASC
