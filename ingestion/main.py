import functions_framework
import json
from os import getenv
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timedelta
from io import StringIO
from google.cloud import bigquery, storage

PROJECT_ID = getenv("PROJECT_ID")
DATASET_ID = getenv("DATASET_ID")
TABLE_ID = getenv("TABLE_ID")
BUCKET_NAME = getenv("BUCKET_NAME")

# --- HELPER: DATABASE STATE ---
# Before downloading anything, we need to know: where did we stop yesterday?
# This ensures we only download *new* data (Incremental Load), saving bandwidth and time.
def get_db_state():
    """Gets the last recorded trade date and ticker map from BigQuery."""
    client = bigquery.Client()
    
    # 1. Get the latest date in our DB
    date_query = f"SELECT MAX(trade_date) as max_date FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    result = list(client.query(date_query).result())
    
    # Default to 2023-01-01 if the table is empty (First Run)
    max_date = result[0].max_date if result and result[0].max_date else datetime(2023, 1, 1).date()
    
    # 2. Get the list of stocks we care about
    # We fetch the mapping of Symbol -> Company Name so we can attach readable names to new records.
    name_query = f"""
        SELECT symbol, MAX(name) as name
        FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`
        WHERE name IS NOT NULL AND name != '0'
        GROUP BY symbol
    """
    name_results = client.query(name_query).result()
    name_map = {row.symbol: row.name for row in name_results}
    tickers = list(name_map.keys())
    
    return tickers, max_date, name_map

# --- MAIN ENTRY POINT ---
@functions_framework.http
def sync_stocks(request):
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    
    print("--- Starting Sync Job ---")

    # 1. CHECK STATE
    try:
        tickers, last_date, name_map = get_db_state()
        print(f"Found {len(tickers)} tickers. Last DB date: {last_date}")
    except Exception as e:
        print(f"CRITICAL: Error fetching DB state: {e}")
        return f"Database state error: {e}", 500
    
    if not tickers:
        print("No tickers found.")
        return "No tickers found to sync.", 200
        
    # 2. DEFINE DATE RANGE
    # Start from tomorrow relative to the last record.
    start_date = last_date + timedelta(days=1)
    today = datetime.now().date()
    
    # IDEMPOTENCY CHECK:
    # If we already have data up to today, STOP.
    # This prevents duplicates if the scheduler accidentally runs twice.
    if start_date > today:
        print("Database is already up to date.")
        return "Database is already up to date.", 200

    print(f"Attempting to fetch data from {start_date} to {today}")

    # 3. NORMALIZE TICKERS
    # Yahoo Finance uses hyphens (BRK-B) but many DBs use dots (BRK.B).
    yf_map = {t: t.replace('.', '-') for t in tickers}
    yf_tickers = list(yf_map.values())

    # 4. DOWNLOAD DATA (EXTRACT)
    try:
        data = yf.download(
            yf_tickers, 
            start=start_date.strftime('%Y-%m-%d'), 
            end=(today + timedelta(days=1)).strftime('%Y-%m-%d'), 
            group_by='ticker', 
            auto_adjust=False,
            threads=True # Use parallel threads for speed
        )
    except Exception as e:
        print(f"yfinance download failed: {e}")
        return f"yfinance download failed: {e}", 500

    if data.empty:
        print("yfinance returned no data (Market closed or holiday).")
        return "No new market data available.", 200

    # 5. TRANSFORM DATA
    daily_records = []
    
    is_multi_index = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    print(f"Processing {len(actual_dates)} days of data...")

    for date_str in actual_dates:
        current_data_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # SAFETY CHECK:
        # Even though we asked for `start_date`, sometimes APIs return overlapping data.
        # We explicitly skip any date we already have to guarantee uniqueness.
        if current_data_date <= last_date:
            print(f"Skipping data for {date_str} (already exists in DB).")
            continue

        for original_ticker in tickers:
            try:
                yf_ticker = yf_map[original_ticker]
                
                # Handle MultiIndex vs SingleIndex (depends on # of tickers downloaded)
                if is_multi_index:
                    if yf_ticker not in data.columns.levels[0]:
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    ticker_data = data.loc[date_str]

                # SKIP EMPTY/CLOSED DAYS
                if pd.isna(ticker_data['Close']): 
                    continue
                
                # FALLBACK LOGIC:
                # Sometimes 'Open' is missing. Use 'Close' as a fallback to prevent crashes.
                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(ticker_data['Close'])
                company_name = name_map.get(original_ticker, original_ticker) 

                record = {
                    "symbol": original_ticker,
                    "name": company_name,
                    "trade_date": date_str,
                    "open_price": open_p,
                    "close_price": float(ticker_data['Close']),
                    "high_price": float(ticker_data['High']),
                    "low_price": float(ticker_data['Low']),
                    "volume": int(ticker_data['Volume'])
                }
                daily_records.append(record)

            except Exception as e:
                print(f"Skipping {original_ticker} on {date_str}: {e}")
                continue
        
    if daily_records:
        print(f"Uploading {len(daily_records)} records to GCS and BigQuery...")
        
        # 6. LOAD TO GCS (BRONZE LAYER)
        # We save the raw JSON to Cloud Storage first.
        # Why? If the BigQuery load fails, or if we need to debug data quality later,
        # we have the exact raw file in the bucket (Audit Trail).
        file_date_str = today.strftime('%Y%m%d')
        blob_name = f"sync/stock_{file_date_str}.json"
        
        storage_client.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
            json.dumps(daily_records), content_type='application/json'
        )
        
        # 7. LOAD TO BIGQUERY (SILVER/GOLD LAYER)
        # We load directly from the JSON string (newline delimited) into BQ.
        ndjson = "\n".join([json.dumps(r) for r in daily_records])
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND # Append, don't overwrite!
        )
        
        try:
            bq_client.load_table_from_file(
                StringIO(ndjson), 
                f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}", 
                job_config=job_config
            ).result()
            print("BigQuery load complete.")
        except Exception as e:
            print(f"BigQuery Load Failed: {e}")
            return f"BigQuery Load Failed: {e}", 500

        # 8. TRIGGER BACKEND REFRESH (EVENT DRIVEN)
        # We notify the FastAPI backend that new data is ready.
        # This allows the backend to update its in-memory DuckDB cache immediately
        # without waiting for a scheduled restart.
        try:
            api_url = getenv("BACKEND_API_URL")
            if api_url:
                refresh_endpoint = f"{api_url}/api/admin/refresh"
                # Short timeout because we don't need to wait for the refresh to finish, just trigger it.
                requests.post(refresh_endpoint, timeout=(3.05, 5))
                print(f"Webhook triggered: {api_url}")
            else:
                print("Webhook skipped: BACKEND_API_URL env var not set.")
        except requests.exceptions.ReadTimeout:
            # This is actually a success: we successfully sent the request, 
            # we just didn't wait around for the 200 OK.
            print("Webhook sent (ReadTimeout ignored).")
        except Exception as e:
            print(f"Webhook warning: {e}")

        return f"Successfully synced {len(daily_records)} records.", 200

    else:
        print("Data downloaded, but all dates were duplicates or empty (Weekend/Holiday).")
        return "No new valid records to insert.", 200