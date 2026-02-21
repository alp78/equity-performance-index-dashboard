WITH
-- 1. Raw stock data — ALL valid sectors/industries in the index
raw AS (
    SELECT symbol, sector, industry,
           strftime(trade_date, '%Y-%m-%d') as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')
      AND industry IS NOT NULL AND industry NOT IN ('N/A', '0', '')
      AND close IS NOT NULL AND close > 0
),

-- 2. Per-stock base price (first close in the full period)
bases AS (
    SELECT symbol,
           FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY time) as base_close
    FROM raw
    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time) = 1
),

-- 3. Per-stock normalized % change from its own base
per_stock_pct AS (
    SELECT r.symbol, r.sector, r.industry, r.time,
           ((r.close - b.base_close) / b.base_close) * 100 as pct
    FROM raw r
    JOIN bases b ON r.symbol = b.symbol
),

-- 4. Per-industry unified timeline (each industry has its own date grid)
all_dates_per_industry AS (
    SELECT DISTINCT sector, industry, time FROM per_stock_pct
),
all_symbols_per_industry AS (
    SELECT DISTINCT sector, industry, symbol FROM per_stock_pct
),

-- 5. Cross-join within each industry: every stock x every date for that industry
grid AS (
    SELECT s.sector, s.industry, s.symbol, d.time
    FROM all_symbols_per_industry s
    JOIN all_dates_per_industry d
      ON s.sector = d.sector AND s.industry = d.industry
),

-- 6. Left join actual pct values onto the grid
with_gaps AS (
    SELECT g.sector, g.industry, g.symbol, g.time, p.pct
    FROM grid g
    LEFT JOIN per_stock_pct p ON g.symbol = p.symbol AND g.time = p.time
),

-- 7. Forward-fill: carry last known pct value forward for each stock
filled AS (
    SELECT sector, industry, symbol, time,
           LAST_VALUE(pct IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as pct
    FROM with_gaps
),

-- 8. Average the per-stock % changes per industry per day, include stock count
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
