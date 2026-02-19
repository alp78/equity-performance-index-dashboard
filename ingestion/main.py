"""
Exchange Dashboard — Daily Data Sync (Cloud Function)
=====================================================
Scheduled ETL job that keeps BigQuery up to date with the latest stock prices.

Pipeline:  Yahoo Finance → Transform → GCS (audit trail) → BigQuery (warehouse)

Scheduling Strategy:
  Two Cloud Scheduler jobs trigger this function at different times,
  each targeting the index whose market has just closed:

    - 18:15 CET → POST { "index": "stoxx50" }   (EU markets close at 17:30 CET)
    - 22:45 CET → POST { "index": "sp500" }      (US markets close at 16:00 EST / 22:00 CET)

  If called without a body (or with { "index": "all" }), syncs all indices.

  Each invocation retries up to 3 times with a 60-second delay if Yahoo
  hasn't published the day's data yet (common on volatile days when
  exchanges delay publishing final settlement prices).

Environment variables:
  PROJECT_ID       — GCP project
  DATASET_ID       — BigQuery dataset
  BUCKET_NAME      — GCS bucket for raw JSON audit trail
  SP500_TABLE_ID   — BigQuery table for S&P 500 (default: sp500_prices)
  STOXX50_TABLE_ID — BigQuery table for STOXX 50 (default: stoxx50_prices)
  BACKEND_API_URL  — Backend URL for the post-sync webhook (optional)
"""

import functions_framework
import json
from os import getenv
import pandas as pd
import yfinance as yf
import requests
import time
from datetime import datetime, timedelta
from io import StringIO
from google.cloud import bigquery, storage


# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ID  = getenv("PROJECT_ID")
DATASET_ID  = getenv("DATASET_ID")
BUCKET_NAME = getenv("BUCKET_NAME")

# Each market index maps to its own BigQuery table.
# To add a new index, add an entry here — the sync logic adapts automatically.
INDICES = {
    "sp500": {
        "table_id": getenv("SP500_TABLE_ID", "sp500_prices"),
    },
    "stoxx50": {
        "table_id": getenv("STOXX50_TABLE_ID", "stoxx50_prices"),
    },
}

# Retry configuration for delayed data availability
MAX_RETRIES = 3         # Number of attempts if Yahoo returns no data
RETRY_DELAY_SEC = 60    # Wait between retries (60s = 1 minute)

# Yahoo Finance exchange suffixes that must NOT be converted.
# European tickers like DB1.DE use a dot suffix to identify the exchange.
# US tickers like BRK.B use dots for share classes → converted to hyphens.
EXCHANGE_SUFFIXES = {
    '.DE', '.PA', '.AS', '.BR', '.MI', '.MC', '.HE', '.VI',   # Europe
    '.L',  '.SW', '.ST', '.CO', '.OL',                         # UK, Swiss, Nordic
    '.T',  '.HK', '.SI', '.AX', '.NZ', '.SA', '.MX',          # Asia, Oceania, LatAm
    '.TO', '.V',                                                 # Canada
}


# ============================================================================
# HELPERS
# ============================================================================

def to_yf_ticker(db_ticker):
    """
    Convert a database ticker to Yahoo Finance format.
    European exchange suffixes are preserved; US share-class dots become hyphens.
    """
    for suffix in EXCHANGE_SUFFIXES:
        if db_ticker.upper().endswith(suffix.upper()):
            return db_ticker
    return db_ticker.replace('.', '-')


def get_full_table_ref(table_id):
    """Build the fully-qualified BigQuery table reference."""
    return f"{PROJECT_ID}.{DATASET_ID}.{table_id}"


# ============================================================================
# STEP 1 — CHECK DATABASE STATE
# ============================================================================

def get_db_state(bq_client, table_ref):
    """
    Query BigQuery to find where we left off.
    Returns (tickers, max_date, name_map, sector_map, industry_map) for incremental loading.
    """
    # Most recent date (defaults to 2023-01-01 on first run)
    date_query = f"SELECT MAX(trade_date) as max_date FROM `{table_ref}`"
    result = list(bq_client.query(date_query).result())
    max_date = result[0].max_date if result and result[0].max_date else datetime(2023, 1, 1).date()

    # Symbol → name, sector, industry mapping (latest non-null values)
    meta_query = f"""
        SELECT symbol,
            MAX(name) as name,
            MAX(sector) as sector,
            MAX(industry) as industry
        FROM `{table_ref}`
        WHERE name IS NOT NULL AND name != '0'
        GROUP BY symbol
    """
    meta_results = bq_client.query(meta_query).result()
    name_map = {}
    sector_map = {}
    industry_map = {}
    for row in meta_results:
        name_map[row.symbol] = row.name
        if row.sector and row.sector not in ('N/A', '0', ''):
            sector_map[row.symbol] = row.sector
        if row.industry and row.industry not in ('N/A', '0', ''):
            industry_map[row.symbol] = row.industry

    tickers = list(name_map.keys())
    return tickers, max_date, name_map, sector_map, industry_map


# ============================================================================
# STEP 2 — DOWNLOAD & TRANSFORM (with retry for delayed data)
# ============================================================================

def download_and_transform(tickers, name_map, sector_map, industry_map, start_date, today, last_date):
    """
    Download price data from Yahoo Finance with automatic retry.
    Fetches sector/industry from yf.Ticker.info for any tickers missing them.
    """
    if not tickers:
        return []

    yf_map = {t: to_yf_ticker(t) for t in tickers}
    yf_tickers = list(yf_map.values())

    # Fetch sector/industry for tickers that don't have them cached
    missing_meta = [t for t in tickers if t not in sector_map or t not in industry_map]
    if missing_meta:
        print(f"  Fetching sector/industry for {len(missing_meta)} tickers...")
        for ticker in missing_meta:
            try:
                yf_ticker = yf_map[ticker]
                info = yf.Ticker(yf_ticker).info
                s = info.get('sector', 'N/A')
                i = info.get('industry', 'N/A')
                if s and s not in ('N/A', ''):
                    sector_map[ticker] = s
                if i and i not in ('N/A', ''):
                    industry_map[ticker] = i
            except Exception as e:
                print(f"    Could not fetch info for {ticker}: {e}")
            time.sleep(0.1)  # Rate limit

    print(f"  Downloading {len(yf_tickers)} tickers from Yahoo Finance...")
    print(f"  Sample mappings: {dict(list(yf_map.items())[:5])}")

    # Retry loop: wait for Yahoo to have the data ready
    data = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = yf.download(
                yf_tickers,
                start=start_date.strftime('%Y-%m-%d'),
                end=(today + timedelta(days=1)).strftime('%Y-%m-%d'),
                group_by='ticker',
                auto_adjust=False,
                threads=True,
            )
        except Exception as e:
            print(f"  yfinance download failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            data = None

        if data is not None and not data.empty:
            # Check if today's data is actually present
            actual_dates = data.index.strftime('%Y-%m-%d').unique()
            if today.strftime('%Y-%m-%d') in actual_dates:
                print(f"  Data confirmed for {today} on attempt {attempt}.")
                break
            else:
                print(f"  Today's data not yet available (attempt {attempt}/{MAX_RETRIES}).")

        if attempt < MAX_RETRIES:
            print(f"  Retrying in {RETRY_DELAY_SEC}s...")
            time.sleep(RETRY_DELAY_SEC)

    if data is None or data.empty:
        print("  No data after all retries (market closed or holiday).")
        return []

    # Transform into flat records
    records = []
    is_multi_index = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    print(f"  Processing {len(actual_dates)} days for {len(tickers)} tickers...")

    if is_multi_index:
        returned_tickers = list(data.columns.get_level_values(0).unique())
        print(f"  Yahoo returned {len(returned_tickers)} tickers: {returned_tickers[:10]}...")

    for date_str in actual_dates:
        current_data_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        # Skip dates we already have (idempotency guard)
        if current_data_date <= last_date:
            continue

        for original_ticker in tickers:
            try:
                yf_ticker = yf_map[original_ticker]

                if is_multi_index:
                    if yf_ticker not in data.columns.levels[0]:
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    ticker_data = data.loc[date_str]

                if pd.isna(ticker_data['Close']):
                    continue

                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(ticker_data['Close'])
                company_name = name_map.get(original_ticker, original_ticker)

                records.append({
                    "symbol":      original_ticker,
                    "name":        company_name,
                    "trade_date":  date_str,
                    "open_price":  open_p,
                    "close_price": float(ticker_data['Close']),
                    "high_price":  float(ticker_data['High']),
                    "low_price":   float(ticker_data['Low']),
                    "volume":      int(ticker_data['Volume']),
                    "sector":      sector_map.get(original_ticker, 'N/A'),
                    "industry":    industry_map.get(original_ticker, 'N/A'),
                })
            except Exception as e:
                print(f"  Skipping {original_ticker} on {date_str}: {e}")
                continue

    return records


# ============================================================================
# STEP 3 — LOAD TO GCS & BIGQUERY
# ============================================================================

def load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, gcs_prefix, today):
    """Two-stage load: GCS (audit trail) then BigQuery (warehouse)."""
    file_date_str = today.strftime('%Y%m%d')
    blob_name = f"sync/{gcs_prefix}_{file_date_str}.json"

    storage_client.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
        json.dumps(records), content_type='application/json'
    )
    print(f"  GCS: uploaded {blob_name}")

    ndjson = "\n".join([json.dumps(r) for r in records])
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    bq_client.load_table_from_file(
        StringIO(ndjson), table_ref, job_config=job_config,
    ).result()
    print(f"  BigQuery: loaded {len(records)} records into {table_ref}")


# ============================================================================
# SYNC LOGIC (per index)
# ============================================================================

def sync_index(bq_client, storage_client, index_key, config, today):
    """
    Run the full ETL pipeline for a single index.
    Returns (record_count, status_message).
    """
    table_ref = get_full_table_ref(config["table_id"])
    print(f"\n[{index_key.upper()}] Processing (table: {table_ref})...")

    # 1. Check state
    try:
        tickers, last_date, name_map, sector_map, industry_map = get_db_state(bq_client, table_ref)
        print(f"  Found {len(tickers)} tickers. Last DB date: {last_date}")
    except Exception as e:
        print(f"  CRITICAL: DB state error for {index_key}: {e}")
        return 0, f"Error: {e}"

    if not tickers:
        print(f"  No tickers found for {index_key}.")
        return 0, "No tickers"

    # 2. Date range
    start_date = last_date + timedelta(days=1)
    if start_date > today:
        print(f"  {index_key} is already up to date.")
        return 0, "Up to date"

    print(f"  Fetching data from {start_date} to {today}")

    # 3. Download & transform (with retry)
    records = download_and_transform(tickers, name_map, sector_map, industry_map, start_date, today, last_date)
    if not records:
        return 0, "No new data"

    # 4. Load
    try:
        load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, index_key, today)
        return len(records), f"Synced {len(records)} records"
    except Exception as e:
        print(f"  Load failed for {index_key}: {e}")
        return 0, f"Load error: {e}"


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

@functions_framework.http
def sync_stocks(request):
    """
    HTTP-triggered Cloud Function. Accepts an optional JSON body to target
    a specific index:
    
      POST { "index": "stoxx50" }  → sync only STOXX 50
      POST { "index": "sp500" }    → sync only S&P 500
      POST { "index": "all" }      → sync all indices (default)
      POST (no body)               → sync all indices
    
    Cloud Scheduler setup (two jobs):
      - stoxx50-sync: 18:15 CET daily → body: { "index": "stoxx50" }
      - sp500-sync:   22:45 CET daily → body: { "index": "sp500" }
    """
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    today = datetime.now().date()

    # Parse which index to sync from the request body
    target_index = "all"
    try:
        body = request.get_json(silent=True) or {}
        target_index = body.get("index", "all").lower()
    except:
        pass

    # Determine which indices to process
    if target_index in INDICES:
        indices_to_sync = {target_index: INDICES[target_index]}
    else:
        indices_to_sync = INDICES

    print(f"--- Starting Sync Job (target: {target_index}) ---")

    total_records = 0
    results = {}

    for index_key, config in indices_to_sync.items():
        count, status = sync_index(bq_client, storage_client, index_key, config, today)
        total_records += count
        results[index_key] = status

    # Notify the backend to refresh its DuckDB cache
    if total_records > 0:
        try:
            api_url = getenv("BACKEND_API_URL")
            if api_url:
                refresh_endpoint = f"{api_url}/api/admin/refresh"
                requests.post(refresh_endpoint, timeout=(3.05, 5))
                print(f"\nWebhook triggered: {refresh_endpoint}")
            else:
                print("\nWebhook skipped: BACKEND_API_URL not set.")
        except requests.exceptions.ReadTimeout:
            print("Webhook sent (ReadTimeout ignored).")
        except Exception as e:
            print(f"Webhook warning: {e}")

    summary = f"Sync complete (target: {target_index}). Total: {total_records} records. Details: {json.dumps(results)}"
    print(f"\n--- {summary} ---")
    return summary, 200