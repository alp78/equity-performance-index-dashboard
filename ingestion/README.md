# Ingestion — Cloud Function (Daily EOD Sync)

Google Cloud Function that syncs stock and index-level OHLCV prices from Yahoo Finance into BigQuery on a daily schedule.

## Stack

- **Cloud Functions** (2nd gen, Python 3.10)
- **Cloud Scheduler** — triggers daily via HTTP POST
- **Yahoo Finance** (`yfinance`) — data source
- **BigQuery** — destination tables
- **Cloud Storage** — NDJSON archive before BQ load

## Pipeline

1. **Gap Detection** — Query BQ for each symbol's last date + interior gaps (16-day lookback)
2. **Download** — Bulk `yfinance` download for gap symbols, with batch fallback if coverage < 30%
3. **Load** — Write NDJSON to GCS (archive) + append to BigQuery
4. **Notify** — POST to backend `/api/admin/refresh/{index}` to reload DuckDB cache

## Covered Indices

| Key | Index | Ticker |
|---|---|---|
| `stoxx50` | EURO STOXX 50 | ^STOXX50E |
| `sp500` | S&P 500 | ^GSPC |
| `ftse100` | FTSE 100 | ^FTSE |
| `nikkei225` | Nikkei 225 | ^N225 |
| `csi300` | CSI 300 | 000300.SS |
| `nifty50` | Nifty 50 | ^NSEI |

## Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally via functions-framework
functions-framework --target=sync_stocks --port=8081

# Trigger sync for a single index
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d '{"index": "sp500"}'

# Trigger sync for all indices
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d '{"index": "all"}'
```

## Deployment

```bash
gcloud functions deploy sync-stocks \
  --gen2 --runtime=python310 \
  --region=europe-west3 \
  --trigger-http \
  --allow-unauthenticated \
  --memory=512Mi --timeout=540 \
  --entry-point=sync_stocks \
  --project=esg-analytics-poc
```

## Environment Variables

| Variable | Description |
|---|---|
| `PROJECT_ID` | GCP project ID (default: `esg-analytics-poc`) |
| `DATASET_ID` | BigQuery dataset (default: `stock_exchange`) |
| `BUCKET_NAME` | GCS archive bucket |
| `BACKEND_URL` | Backend API URL for refresh notification |
