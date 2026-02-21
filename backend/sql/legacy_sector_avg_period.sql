-- computes daily average close price for a sector over a recent lookback period
WITH sector_stocks AS (
    SELECT symbol,
           strftime(trade_date, '%Y-%m-%d') as time,
           CAST(close AS FLOAT) as close
    FROM {table}
    WHERE sector = ? AND sector IS NOT NULL
      AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'
),
daily_avg AS (
    SELECT time, AVG(close) as avg_close
    FROM sector_stocks
    GROUP BY time ORDER BY time ASC
)
SELECT time, avg_close as close FROM daily_avg
