"""
Cloud Function — Daily Price Sync (Multi-Index)
================================================
Supports 6 market indices with market-hours-aware scheduling.

Smart scheduling:
  - Each index has a sync_after_utc hour: the function skips indices
    whose market hasn't closed yet (avoids wasted BigQuery + Yahoo calls)
  - Cloud Scheduler fires every 30 min; only ready markets are processed
  - After 3 failed retries, logs a CRITICAL alert for monitoring

POST body:
  { "index": "sp500" }       → sync only S&P 500
  { "index": "all" }         → sync all ready indices
  { "index": "all_force" }   → sync all indices regardless of market hours
"""

import functions_framework
from google.cloud import bigquery, storage
from os import getenv
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import json
import time
import requests
import logging
from io import StringIO

# Configure structured logging for Cloud Logging
logger = logging.getLogger("sync")
logger.setLevel(logging.INFO)

PROJECT_ID  = getenv("PROJECT_ID")
DATASET_ID  = getenv("DATASET_ID")
BUCKET_NAME = getenv("BUCKET_NAME")

# Each index: table_id + sync_after_utc (hour after which market data is expected)
# This prevents the function from trying to fetch data before market close.
INDICES = {
    "stoxx50": {
        "table_id": getenv("STOXX50_TABLE_ID", "stoxx50_prices"),
        "sync_after_utc": 17,  # EU markets close ~16:30 CET = 15:30 UTC + buffer
    },
    "sp500": {
        "table_id": getenv("SP500_TABLE_ID", "sp500_prices"),
        "sync_after_utc": 21,  # US markets close 16:00 ET = 21:00 UTC
    },
    "ftse100": {
        "table_id": getenv("FTSE100_TABLE_ID", "ftse100_prices"),
        "sync_after_utc": 17,  # LSE closes 16:30 GMT = 16:30 UTC + buffer
    },
    "nikkei225": {
        "table_id": getenv("NIKKEI225_TABLE_ID", "nikkei225_prices"),
        "sync_after_utc": 7,   # TSE closes 15:00 JST = 06:00 UTC + buffer
    },
    "csi300": {
        "table_id": getenv("CSI300_TABLE_ID", "csi300_prices"),
        "sync_after_utc": 8,   # SSE closes 15:00 CST = 07:00 UTC + buffer
    },
    "nifty50": {
        "table_id": getenv("NIFTY50_TABLE_ID", "nifty50_prices"),
        "sync_after_utc": 11,  # NSE closes 15:30 IST = 10:00 UTC + buffer
    },
}

MAX_RETRIES = 3
RETRY_DELAY_SEC = 90

# Yahoo Finance exchange suffixes
EXCHANGE_SUFFIXES = {
    '.DE', '.PA', '.AS', '.BR', '.MI', '.MC', '.HE', '.VI',
    '.L',  '.SW', '.ST', '.CO', '.OL',
    '.T',  '.HK', '.SI', '.AX', '.NZ', '.SA', '.MX',
    '.TO', '.V',  '.NS', '.BO', '.SS', '.SZ',
}


# ============================================================================
# HELPERS
# ============================================================================

def to_yf_ticker(db_ticker):
    for suffix in EXCHANGE_SUFFIXES:
        if db_ticker.upper().endswith(suffix.upper()):
            return db_ticker
    return db_ticker.replace('.', '-')

def get_full_table_ref(short_table_id):
    return f"{PROJECT_ID}.{DATASET_ID}.{short_table_id}"

def is_market_ready(config, now_utc):
    """Check if the market for this index has closed and data should be available."""
    sync_hour = config.get("sync_after_utc", 0)
    return now_utc.hour >= sync_hour

def is_weekend(today):
    return today.weekday() >= 5  # Saturday=5, Sunday=6


# ============================================================================
# STEP 1 — CHECK DATABASE STATE
# ============================================================================

def get_db_state(bq_client, table_ref):
    date_query = f"SELECT MAX(trade_date) as max_date FROM `{table_ref}`"
    result = list(bq_client.query(date_query).result())
    max_date = result[0].max_date if result and result[0].max_date else datetime(2023, 1, 1).date()

    meta_query = f"""
        SELECT symbol, MAX(name) as name, MAX(sector) as sector, MAX(industry) as industry
        FROM `{table_ref}`
        WHERE name IS NOT NULL AND name != '0'
        GROUP BY symbol
    """
    meta_results = bq_client.query(meta_query).result()
    name_map, sector_map, industry_map = {}, {}, {}
    for row in meta_results:
        name_map[row.symbol] = row.name
        if row.sector and row.sector not in ('N/A', '0', ''):
            sector_map[row.symbol] = row.sector
        if row.industry and row.industry not in ('N/A', '0', ''):
            industry_map[row.symbol] = row.industry

    return list(name_map.keys()), max_date, name_map, sector_map, industry_map


# ============================================================================
# STEP 2 — DOWNLOAD & TRANSFORM
# ============================================================================

def download_and_transform(tickers, name_map, sector_map, industry_map, start_date, today, last_date):
    if not tickers:
        return []

    yf_map = {t: to_yf_ticker(t) for t in tickers}
    yf_tickers = list(yf_map.values())

    # Fetch missing sector/industry
    missing = [t for t in tickers if t not in sector_map or t not in industry_map]
    if missing:
        logger.info(f"  Fetching sector/industry for {len(missing)} tickers...")
        for ticker in missing:
            try:
                info = yf.Ticker(yf_map[ticker]).info
                s, i = info.get('sector', 'N/A'), info.get('industry', 'N/A')
                if s and s != 'N/A': sector_map[ticker] = s
                if i and i != 'N/A': industry_map[ticker] = i
            except:
                pass
            time.sleep(0.1)

    logger.info(f"  Downloading {len(yf_tickers)} tickers from Yahoo Finance...")

    data = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = yf.download(
                yf_tickers,
                start=start_date.strftime('%Y-%m-%d'),
                end=(today + timedelta(days=1)).strftime('%Y-%m-%d'),
                group_by='ticker', auto_adjust=False, threads=True,
            )
        except Exception as e:
            logger.warning(f"  yfinance download failed (attempt {attempt}): {e}")
            data = None

        if data is not None and not data.empty:
            actual_dates = data.index.strftime('%Y-%m-%d').unique()
            if today.strftime('%Y-%m-%d') in actual_dates:
                logger.info(f"  Data confirmed for {today} on attempt {attempt}.")
                break
            else:
                logger.info(f"  Today's data not yet available (attempt {attempt}/{MAX_RETRIES}).")

        if attempt < MAX_RETRIES:
            logger.info(f"  Retrying in {RETRY_DELAY_SEC}s...")
            time.sleep(RETRY_DELAY_SEC)

    if data is None or data.empty:
        return []

    records = []
    is_multi = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    for date_str in actual_dates:
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        if current_date <= last_date:
            continue

        for original_ticker in tickers:
            try:
                yf_ticker = yf_map[original_ticker]
                if is_multi:
                    if yf_ticker not in data.columns.levels[0]:
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    ticker_data = data.loc[date_str]

                if pd.isna(ticker_data['Close']):
                    continue

                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(ticker_data['Close'])

                records.append({
                    "symbol":      original_ticker,
                    "name":        name_map.get(original_ticker, original_ticker),
                    "trade_date":  date_str,
                    "open_price":  open_p,
                    "close_price": float(ticker_data['Close']),
                    "high_price":  float(ticker_data['High']),
                    "low_price":   float(ticker_data['Low']),
                    "volume":      int(ticker_data['Volume']),
                    "sector":      sector_map.get(original_ticker, 'N/A'),
                    "industry":    industry_map.get(original_ticker, 'N/A'),
                })
            except:
                continue

    return records


# ============================================================================
# STEP 3 — LOAD TO GCS & BIGQUERY
# ============================================================================

def load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, gcs_prefix, today):
    file_date_str = today.strftime('%Y%m%d')
    blob_name = f"sync/{gcs_prefix}_{file_date_str}.json"

    storage_client.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
        json.dumps(records), content_type='application/json'
    )

    ndjson = "\n".join([json.dumps(r) for r in records])
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    bq_client.load_table_from_file(StringIO(ndjson), table_ref, job_config=job_config).result()
    logger.info(f"  BigQuery: loaded {len(records)} records into {table_ref}")


# ============================================================================
# SYNC LOGIC
# ============================================================================

def sync_index(bq_client, storage_client, index_key, config, today):
    table_ref = get_full_table_ref(config["table_id"])
    logger.info(f"\n[{index_key.upper()}] Processing (table: {table_ref})...")

    try:
        tickers, last_date, name_map, sector_map, industry_map = get_db_state(bq_client, table_ref)
        logger.info(f"  Found {len(tickers)} tickers. Last DB date: {last_date}")
    except Exception as e:
        logger.critical(f"  ALERT: DB state error for {index_key}: {e}")
        return 0, f"Error: {e}"

    if not tickers:
        return 0, "No tickers"

    start_date = last_date + timedelta(days=1)
    if start_date > today:
        return 0, "Up to date"

    logger.info(f"  Fetching data from {start_date} to {today}")

    records = download_and_transform(tickers, name_map, sector_map, industry_map, start_date, today, last_date)

    if not records:
        # After all retries, still no data — log CRITICAL alert
        logger.critical(
            f"  ALERT: No data retrieved for {index_key} after {MAX_RETRIES} retries. "
            f"Expected data for {today}. Market may be closed or Yahoo API issue."
        )
        return 0, "No new data (ALERT logged)"

    try:
        load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, index_key, today)
        return len(records), f"Synced {len(records)} records"
    except Exception as e:
        logger.critical(f"  ALERT: Load failed for {index_key}: {e}")
        return 0, f"Load error: {e}"


# ============================================================================
# ENTRY POINT
# ============================================================================

@functions_framework.http
def sync_stocks(request):
    """
    HTTP-triggered Cloud Function.

    POST { "index": "sp500" }       → sync only S&P 500
    POST { "index": "all" }         → sync all market-ready indices
    POST { "index": "all_force" }   → sync ALL indices (ignore market hours)
    POST (no body)                  → sync all market-ready indices

    Cloud Scheduler: fire every 30 min → function auto-skips non-ready markets.
    """
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    now_utc = datetime.utcnow()
    today = now_utc.date()

    # Skip weekends entirely
    if is_weekend(today):
        return f"Weekend — no markets open. Skipping.", 200

    target_index = "all"
    try:
        body = request.get_json(silent=True) or {}
        target_index = body.get("index", "all").lower()
    except:
        pass

    force = target_index == "all_force"

    # Determine which indices to process
    if target_index in INDICES:
        indices_to_sync = {target_index: INDICES[target_index]}
    else:
        indices_to_sync = INDICES

    logger.info(f"--- Sync Job (target: {target_index}, force: {force}, UTC: {now_utc.isoformat()}) ---")

    total_records = 0
    results = {}
    synced_indices = []

    for index_key, config in indices_to_sync.items():
        # Smart skip: don't process if market hasn't closed yet
        if not force and not is_market_ready(config, now_utc):
            results[index_key] = "Skipped (market not yet closed)"
            logger.info(f"  [{index_key}] Skipped — market not ready (sync_after_utc={config['sync_after_utc']}, current_utc_hour={now_utc.hour})")
            continue

        count, status = sync_index(bq_client, storage_client, index_key, config, today)
        total_records += count
        results[index_key] = status
        if count > 0:
            synced_indices.append(index_key)

    # Notify backend — per-index refresh only (not full refresh)
    if synced_indices:
        api_url = getenv("BACKEND_API_URL")
        if api_url:
            for idx in synced_indices:
                try:
                    requests.post(f"{api_url}/api/admin/refresh/{idx}", timeout=(3.05, 5))
                    logger.info(f"  Webhook sent for {idx}")
                except requests.exceptions.ReadTimeout:
                    pass
                except Exception as e:
                    logger.warning(f"  Webhook warning for {idx}: {e}")

    summary = f"Sync complete (target: {target_index}). Total: {total_records} records. Details: {json.dumps(results)}"
    logger.info(f"\n--- {summary} ---")
    return summary, 200