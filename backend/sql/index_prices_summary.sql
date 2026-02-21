-- returns the latest price summary with daily change percent for all index symbols
SELECT symbol, name, currency, exchange, trade_date,
    CAST(open AS FLOAT) as open,
    CAST(close AS FLOAT) as last_price,
    CAST(high AS FLOAT) as high,
    CAST(low AS FLOAT) as low,
    CAST(volume AS BIGINT) as volume,
    CAST(((close - prev_price) / NULLIF(prev_price, 0)) * 100 AS FLOAT) as daily_change_pct
FROM latest_index_prices
ORDER BY symbol
