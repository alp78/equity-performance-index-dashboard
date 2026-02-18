"""
Exchange Dashboard — Daily Data Sync (Cloud Function)
=====================================================
Scheduled ETL job that keeps BigQuery up to date with the latest stock prices.

Pipeline:  Yahoo Finance → Transform → GCS (audit trail) → BigQuery (warehouse)

Runs once daily via Cloud Scheduler. Handles multiple market indices
(S&P 500, EURO STOXX 50) in a single invocation — each index is processed
independently so a failure in one doesn't block the others.

After loading new data, triggers a webhook to refresh the backend's
in-memory cache (DuckDB) so the dashboard reflects changes immediately.

Environment variables:
  PROJECT_ID       — GCP project
  DATASET_ID       — BigQuery dataset
  BUCKET_NAME      — GCS bucket for raw JSON audit trail
  SP500_TABLE_ID   — BigQuery table for S&P 500 (default: stock_prices)
  STOXX50_TABLE_ID — BigQuery table for STOXX 50 (default: stoxx50_prices)
  BACKEND_API_URL  — Backend URL for the post-sync webhook (optional)
"""

import functions_framework
import json
from os import getenv
import pandas as pd
import yfinance as yf
import requests
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
        "table_id": getenv("SP500_TABLE_ID", "stock_prices"),
    },
    "stoxx50": {
        "table_id": getenv("STOXX50_TABLE_ID", "stoxx50_prices"),
    },
}

# Yahoo Finance exchange suffixes that must NOT be converted.
# European tickers like DB1.DE or ASML.AS use a dot-based suffix to identify
# the exchange. US tickers like BRK.B use dots for share classes, which Yahoo
# expects as hyphens (BRK-B). This set lets us tell the two apart.
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
    Convert a database ticker to the format Yahoo Finance expects.
    
    European exchange suffixes (DB1.DE, MC.PA) are preserved as-is.
    US share-class dots (BRK.B) are converted to hyphens (BRK-B).
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
    Query BigQuery to find out where we left off.
    
    Returns:
      tickers  — list of symbols we track (e.g. ['AAPL', 'MSFT', ...])
      max_date — the most recent trade date already in the table
      name_map — dict of symbol → company name for attaching readable names
    """
    # Most recent date in the table (defaults to 2023-01-01 on first run)
    date_query = f"SELECT MAX(trade_date) as max_date FROM `{table_ref}`"
    result = list(bq_client.query(date_query).result())
    max_date = result[0].max_date if result and result[0].max_date else datetime(2023, 1, 1).date()

    # Symbol → company name mapping
    name_query = f"""
        SELECT symbol, MAX(name) as name
        FROM `{table_ref}`
        WHERE name IS NOT NULL AND name != '0'
        GROUP BY symbol
    """
    name_results = bq_client.query(name_query).result()
    name_map = {row.symbol: row.name for row in name_results}
    tickers = list(name_map.keys())

    return tickers, max_date, name_map


# ============================================================================
# STEP 2 — DOWNLOAD & TRANSFORM
# ============================================================================

def download_and_transform(tickers, name_map, start_date, today, last_date):
    """
    Download price data from Yahoo Finance and transform it into flat records
    ready for BigQuery insertion.
    
    Handles:
      - Ticker format conversion (DB format → Yahoo format)
      - Skipping dates that already exist in the database
      - Graceful fallback when 'Open' is missing (uses 'Close' instead)
    """
    if not tickers:
        return []

    # Build DB ticker → Yahoo ticker mapping
    yf_map = {t: to_yf_ticker(t) for t in tickers}
    yf_tickers = list(yf_map.values())

    print(f"  Downloading {len(yf_tickers)} tickers from Yahoo Finance...")
    print(f"  Sample mappings: {dict(list(yf_map.items())[:5])}")

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
        print(f"  yfinance download failed: {e}")
        return []

    if data.empty:
        print("  yfinance returned no data (market closed or holiday).")
        return []

    records = []
    is_multi_index = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    print(f"  Processing {len(actual_dates)} days of data for {len(tickers)} tickers...")

    # Log which tickers Yahoo actually returned (helps debug missing data)
    if is_multi_index:
        returned_tickers = list(data.columns.get_level_values(0).unique())
        print(f"  Yahoo returned data for {len(returned_tickers)} tickers: {returned_tickers[:10]}...")

    for date_str in actual_dates:
        current_data_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        # Safety: skip any date we already have (prevents duplicates)
        if current_data_date <= last_date:
            continue

        for original_ticker in tickers:
            try:
                yf_ticker = yf_map[original_ticker]

                # Extract this ticker's row for the current date
                if is_multi_index:
                    if yf_ticker not in data.columns.levels[0]:
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    ticker_data = data.loc[date_str]

                # Skip if market was closed for this ticker on this day
                if pd.isna(ticker_data['Close']):
                    continue

                # Fallback: use Close as Open if Open is missing
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
                })

            except Exception as e:
                print(f"  Skipping {original_ticker} on {date_str}: {e}")
                continue

    return records


# ============================================================================
# STEP 3 — LOAD TO GCS & BIGQUERY
# ============================================================================

def load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, gcs_prefix, today):
    """
    Two-stage load:
      1. GCS  — raw JSON saved as an audit trail (bronze layer)
      2. BigQuery — appended as new rows (silver/gold layer)
    
    If BigQuery fails, the GCS file is still there for recovery or debugging.
    """
    # GCS: save raw JSON for auditability
    file_date_str = today.strftime('%Y%m%d')
    blob_name = f"sync/{gcs_prefix}_{file_date_str}.json"

    storage_client.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
        json.dumps(records), content_type='application/json'
    )
    print(f"  GCS: uploaded {blob_name}")

    # BigQuery: append new records
    ndjson = "\n".join([json.dumps(r) for r in records])
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    bq_client.load_table_from_file(
        StringIO(ndjson),
        table_ref,
        job_config=job_config,
    ).result()
    print(f"  BigQuery: loaded {len(records)} records into {table_ref}")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

@functions_framework.http
def sync_stocks(request):
    """
    HTTP-triggered Cloud Function (called daily by Cloud Scheduler).
    
    For each configured index:
      1. Checks where we left off in BigQuery
      2. Downloads only the missing days from Yahoo Finance
      3. Saves to GCS (audit) and BigQuery (warehouse)
    
    After all indices are processed, fires a webhook to refresh the
    backend's in-memory cache so the dashboard updates immediately.
    
    Idempotent: running twice on the same day is safe — it skips
    dates that already exist.
    """
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    today = datetime.now().date()

    print("--- Starting Multi-Index Sync Job ---")

    total_records = 0
    results = {}

    for index_key, config in INDICES.items():
        table_ref = get_full_table_ref(config["table_id"])
        print(f"\n[{index_key.upper()}] Processing (table: {table_ref})...")

        # 1. Check where we left off
        try:
            tickers, last_date, name_map = get_db_state(bq_client, table_ref)
            print(f"  Found {len(tickers)} tickers. Last DB date: {last_date}")
        except Exception as e:
            print(f"  CRITICAL: Error fetching DB state for {index_key}: {e}")
            results[index_key] = f"Error: {e}"
            continue

        if not tickers:
            print(f"  No tickers found for {index_key}.")
            results[index_key] = "No tickers"
            continue

        # 2. Determine date range (day after last record → today)
        start_date = last_date + timedelta(days=1)
        if start_date > today:
            print(f"  {index_key} is already up to date.")
            results[index_key] = "Up to date"
            continue

        print(f"  Fetching data from {start_date} to {today}")

        # 3. Download from Yahoo & transform
        records = download_and_transform(tickers, name_map, start_date, today, last_date)

        if not records:
            print(f"  No new valid records for {index_key}.")
            results[index_key] = "No new data"
            continue

        # 4. Load to GCS + BigQuery
        try:
            load_to_gcs_and_bq(
                bq_client, storage_client, records,
                table_ref, index_key, today,
            )
            total_records += len(records)
            results[index_key] = f"Synced {len(records)} records"
        except Exception as e:
            print(f"  Load failed for {index_key}: {e}")
            results[index_key] = f"Load error: {e}"
            continue

    # 5. Notify the backend to refresh its in-memory cache (single webhook for all indices)
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
            # Expected: we sent the request successfully but didn't wait for the
            # backend to finish reloading. That's fine — it runs in the background.
            print("Webhook sent (ReadTimeout ignored).")
        except Exception as e:
            print(f"Webhook warning: {e}")

    # 6. Return summary
    summary = f"Sync complete. Total: {total_records} records. Details: {json.dumps(results)}"
    print(f"\n--- {summary} ---")
    return summary, 200