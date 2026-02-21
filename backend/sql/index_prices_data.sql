-- returns normalized percent-change time series with forward-fill for selected index symbols
WITH
raw AS (
    SELECT symbol,
           strftime(trade_date, '%Y-%m-%d') as time,
           CAST(close AS FLOAT) as close,
           CAST(volume AS BIGINT) as volume
    FROM index_prices
    WHERE symbol IN ({placeholders}) {date_clause}
      AND close IS NOT NULL AND close > 0
),

bases AS (
    SELECT symbol,
           FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY time) as base_close
    FROM raw
    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time) = 1
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
