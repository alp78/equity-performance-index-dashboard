SELECT symbol, name, currency, exchange,
    CAST(trade_date AS DATE) as trade_date,
    CAST(open_price AS FLOAT64) as open,
    CAST(close_price AS FLOAT64) as close,
    CAST(high_price AS FLOAT64) as high,
    CAST(low_price AS FLOAT64) as low,
    CAST(volume AS INT64) as volume
FROM `{table_id}`
