CREATE TABLE latest_index_prices AS
SELECT symbol, name, currency, exchange, trade_date, open, close, high, low, volume,
    LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price
FROM index_prices
QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
