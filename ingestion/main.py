import functions_framework
import json
import os
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timedelta
from io import StringIO
from google.cloud import bigquery, storage

# Configuration
PROJECT_ID = ""
DATASET_ID = ""
TABLE_ID = ""
BUCKET_NAME = "d"

def get_db_state():
    """Gets the last recorded trade date and ticker map from BigQuery."""
    client = bigquery.Client()
    
    # 1. Get the max date
    date_query = f"SELECT MAX(trade_date) as max_date FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    result = list(client.query(date_query).result())
    
    # Handle empty table case (default to start of 2023)
    max_date = result[0].max_date if result and result[0].max_date else datetime(2023, 1, 1).date()
    
    # 2. Get the tickers
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

@functions_framework.http
def sync_stocks(request):
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    
    print("--- Starting Sync Job ---")

    # 1. Get DB State
    try:
        tickers, last_date, name_map = get_db_state()
        print(f"Found {len(tickers)} tickers. Last DB date: {last_date}")
    except Exception as e:
        print(f"CRITICAL: Error fetching DB state: {e}")
        return f"Database state error: {e}", 500
    
    if not tickers:
        print("No tickers found.")
        return "No tickers found to sync.", 200
        
    # 2. Calculate Date Range
    start_date = last_date + timedelta(days=1)
    today = datetime.now().date()
    
    # Allow running if start_date is today (to catch up end-of-day data)
    # If start_date is in the future, we stop.
    if start_date > today:
        print("Database is already up to date.")
        return "Database is already up to date.", 200

    print(f"Attempting to fetch data from {start_date} to {today}")

    # 3. Download Data
    yf_map = {t: t.replace('.', '-') for t in tickers}
    yf_tickers = list(yf_map.values())

    # Note: 'end' is exclusive in yfinance, so we add 1 day to cover 'today'
    try:
        data = yf.download(
            yf_tickers, 
            start=start_date.strftime('%Y-%m-%d'), 
            end=(today + timedelta(days=1)).strftime('%Y-%m-%d'), 
            group_by='ticker', 
            auto_adjust=False,
            threads=True 
        )
    except Exception as e:
        print(f"yfinance download failed: {e}")
        return f"yfinance download failed: {e}", 500

    if data.empty:
        print("yfinance returned no data (Market closed or holiday).")
        return "No new market data available.", 200

    # 4. Process Data
    daily_records = []
    
    # Detect if we have a MultiIndex (many tickers) or Flat Index (1 ticker)
    is_multi_index = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    print(f"Processing {len(actual_dates)} days of data...")

    for date_str in actual_dates:
        # FIX: STRICTLY FILTER DUPLICATES
        # yfinance might return the previous Friday's data if you ask for Saturday.
        # We must explicitly check if this date is already in our DB.
        current_data_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        if current_data_date <= last_date:
            print(f"Skipping data for {date_str} (already exists in DB).")
            continue

        for original_ticker in tickers:
            try:
                yf_ticker = yf_map[original_ticker]
                
                # --- SAFE DATA EXTRACTION ---
                if is_multi_index:
                    # Check if ticker exists in the downloaded columns
                    if yf_ticker not in data.columns.levels[0]:
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    # Single ticker case
                    ticker_data = data.loc[date_str]

                # Skip if Close price is NaN (no trade happened)
                if pd.isna(ticker_data['Close']): 
                    continue
                
                # Handle Open Price logic
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
                # Log the specific error for this ticker but don't stop the whole job
                print(f"Skipping {original_ticker} on {date_str}: {e}")
                continue
        
    # 5. Upload if we have records
    if daily_records:
        print(f"Uploading {len(daily_records)} records to GCS and BigQuery...")
        
        # FIX: FORMAT FILENAME WITHOUT DASHES (YYYYMMDD)
        file_date_str = today.strftime('%Y%m%d')
        blob_name = f"sync/stock_{file_date_str}.json"
        
        storage_client.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
            json.dumps(daily_records), content_type='application/json'
        )
        
        # Load to BigQuery
        ndjson = "\n".join([json.dumps(r) for r in daily_records])
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND
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

        # 6. Webhook Trigger
        try:
            api_url = os.getenv("BACKEND_API_URL")
            if api_url:
                refresh_endpoint = f"{api_url}/api/admin/refresh"
                # Short timeout for webhook (fire and forget)
                requests.post(refresh_endpoint, timeout=(3.05, 5))
                print(f"Webhook triggered: {api_url}")
            else:
                print("Webhook skipped: BACKEND_API_URL env var not set.")
        except requests.exceptions.ReadTimeout:
            print("Webhook sent (ReadTimeout ignored).")
        except Exception as e:
            print(f"Webhook warning: {e}")

        return f"Successfully synced {len(daily_records)} records.", 200

    else:
        print("Data downloaded, but all dates were duplicates or empty (Weekend/Holiday).")
        return "No new valid records to insert.", 200