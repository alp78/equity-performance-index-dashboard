SELECT close FROM index_prices
WHERE symbol = ? AND trade_date >= (
    SELECT MAX(trade_date) - INTERVAL '{days} days' FROM index_prices WHERE symbol = ?
)
ORDER BY trade_date ASC LIMIT 1
