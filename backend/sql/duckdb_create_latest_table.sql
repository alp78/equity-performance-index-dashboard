CREATE TABLE {latest_table} AS
SELECT symbol, name, sector, industry, market_index, trade_date, open, close, high, low, volume,
    LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price
FROM {table_name}
QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
