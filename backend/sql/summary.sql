SELECT symbol, name, sector, industry,
    CAST(close AS FLOAT) as last_price,
    CAST(high AS FLOAT) as high,
    CAST(low AS FLOAT) as low,
    CAST(volume AS BIGINT) as volume,
    trade_date,
    CAST(((close - prev_price) / NULLIF(prev_price, 0)) * 100 AS FLOAT) as daily_change_pct
FROM latest_{index}
WHERE market_index = '{index}'
ORDER BY symbol
