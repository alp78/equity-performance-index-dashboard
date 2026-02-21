CREATE TABLE {table_name} AS
SELECT symbol, name, sector, industry, trade_date, open, close, high, low, volume, market_index
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY symbol, trade_date ORDER BY volume DESC
    ) as rn FROM temp_{index_key}
) WHERE rn = 1
