-- precomputes normalized percent-change time series for every industry with forward-fill across all stocks
WITH
raw AS (
    SELECT symbol, sector, industry,
           strftime(trade_date, '%Y-%m-%d') as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
      AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
      AND close IS NOT NULL AND close > 0
),

bases AS (
    SELECT symbol,
           FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY time) as base_close
    FROM raw
    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time) = 1
),

per_stock_pct AS (
    SELECT r.symbol, r.sector, r.industry, r.time,
           ((r.close - b.base_close) / b.base_close) * 100 as pct
    FROM raw r
    JOIN bases b ON r.symbol = b.symbol
),

all_dates_per_industry AS (
    SELECT DISTINCT sector, industry, time FROM per_stock_pct
),
all_symbols_per_industry AS (
    SELECT DISTINCT sector, industry, symbol FROM per_stock_pct
),

grid AS (
    SELECT s.sector, s.industry, s.symbol, d.time
    FROM all_symbols_per_industry s
    JOIN all_dates_per_industry d
      ON s.sector = d.sector AND s.industry = d.industry
),

with_gaps AS (
    SELECT g.sector, g.industry, g.symbol, g.time, p.pct
    FROM grid g
    LEFT JOIN per_stock_pct p ON g.symbol = p.symbol AND g.time = p.time
),

filled AS (
    SELECT sector, industry, symbol, time,
           LAST_VALUE(pct IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as pct
    FROM with_gaps
),

daily_avg AS (
    SELECT sector, industry, time,
           AVG(pct) as pct,
           COUNT(DISTINCT symbol) as stock_count
    FROM filled
    WHERE pct IS NOT NULL
    GROUP BY sector, industry, time
    ORDER BY sector, industry, time ASC
)

SELECT sector, industry, time, pct, stock_count FROM daily_avg
