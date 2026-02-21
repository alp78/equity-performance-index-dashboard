WITH
-- 1. Raw stock data matching the filter
raw AS (
    SELECT symbol,
           strftime(trade_date, '%Y-%m-%d') as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE {sector_clause}{industry_clause}{date_clause}
      AND close IS NOT NULL AND close > 0
),

-- 2. Per-stock base price (first close in the period)
bases AS (
    SELECT symbol,
           FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY time) as base_close
    FROM raw
    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time) = 1
),

-- 3. Per-stock normalized % change from its own base
per_stock_pct AS (
    SELECT r.symbol, r.time,
           ((r.close - b.base_close) / b.base_close) * 100 as pct
    FROM raw r
    JOIN bases b ON r.symbol = b.symbol
),

-- 4. Unified timeline (all distinct dates across ALL stocks in the filter)
all_dates AS (
    SELECT DISTINCT time FROM per_stock_pct ORDER BY time
),
all_symbols AS (
    SELECT DISTINCT symbol FROM per_stock_pct
),

-- 5. Cross-join: every stock x every date (full grid)
grid AS (
    SELECT s.symbol, d.time
    FROM all_symbols s CROSS JOIN all_dates d
),

-- 6. Left join actual pct values onto the grid
with_gaps AS (
    SELECT g.symbol, g.time, p.pct
    FROM grid g
    LEFT JOIN per_stock_pct p ON g.symbol = p.symbol AND g.time = p.time
),

-- 7. Forward-fill: carry last known pct value forward for each stock
filled AS (
    SELECT symbol, time,
           LAST_VALUE(pct IGNORE NULLS) OVER (
               PARTITION BY symbol ORDER BY time
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) as pct
    FROM with_gaps
),

-- 8. Average the per-stock % changes across all stocks per day
daily_avg AS (
    SELECT time, AVG(pct) as avg_pct
    FROM filled
    WHERE pct IS NOT NULL
    GROUP BY time
    ORDER BY time ASC
)

SELECT time, avg_pct as pct FROM daily_avg
