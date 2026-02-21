-- creates the deduplicated index_prices table from temp staging data
CREATE TABLE index_prices AS
SELECT symbol, name, currency, exchange, trade_date, open, close, high, low, volume
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY symbol, trade_date ORDER BY volume DESC
    ) as rn FROM temp_index_prices
) WHERE rn = 1
