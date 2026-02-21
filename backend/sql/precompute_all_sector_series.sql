-- precomputes normalized percent-change time series for every sector with forward-fill across all stocks
WITH
raw AS (
    SELECT symbol, sector,
           strftime(trade_date, '%Y-%m-%d') as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
      AND close IS NOT NULL AND close > 0
),

bases AS (
    SELECT symbol,
           FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY time) as base_close
    FROM raw
    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time) = 1
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
