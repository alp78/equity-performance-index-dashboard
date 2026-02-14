import functions_framework
import json
import os
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timedelta
from io import StringIO
from google.cloud import bigquery, storage

PROJECT_ID = ""
DATASET_ID = ""
TABLE_ID = ""
BUCKET_NAME = ""

def get_db_state():
    client = bigquery.Client()
    date_query = f"SELECT MAX(trade_date) as max_date FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    max_date = list(client.query(date_query).result())[0].max_date
    
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
    
    try:
        tickers, last_date, name_map = get_db_state()
    except Exception as e:
        print(f"Error fetching DB state: {e}")
        return f"Database state error: {e}", 500
    
    if not tickers:
        return "No tickers found to sync.", 200
        
    start_date = last_date + timedelta(days=1)
    today = datetime.now().date()
    
    if start_date >= today:
        return "Database is already up to date.", 200

    yf_map = {t: t.replace('.', '-') for t in tickers}
    yf_tickers = list(yf_map.values())

    data = yf.download(
        yf_tickers, 
        start=start_date.strftime('%Y-%m-%d'), 
        end=today.strftime('%Y-%m-%d'), 
        group_by='ticker', 
        auto_adjust=False,
        threads=True 
    )

    if data.empty:
        return "No new market data available.", 200

    actual_dates = data.index.strftime('%Y-%m-%d').unique()
    data_loaded = False

    for date_str in actual_dates:
        daily_records = []
        for original_ticker in tickers:
            try:
                yf_ticker = yf_map[original_ticker]
                if yf_ticker not in data.columns.levels[0]: 
                    continue
                
                ticker_data = data[yf_ticker].loc[date_str]
                if pd.isna(ticker_data['Close']): 
                    continue
                
                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(ticker_data['Close'])
                company_name = name_map.get(original_ticker, original_ticker) 

                daily_records.append({
                    "symbol": original_ticker,
                    "name": company_name,
                    "trade_date": date_str,
                    "open_price": open_p,
                    "close_price": float(ticker_data['Close']),
                    "high_price": float(ticker_data['High']),
                    "low_price": float(ticker_data['Low']),
                    "volume": int(ticker_data['Volume'])
                })
            except Exception:
                continue
        
        if daily_records:
            blob_name = f"sync/stock_{date_str.replace('-', '')}.json"
            storage_client.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
                json.dumps(daily_records), content_type='application/json'
            )
            
            ndjson = "\n".join([json.dumps(r) for r in daily_records])
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND
            )
            bq_client.load_table_from_file(
                StringIO(ndjson), 
                f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}", 
                job_config=job_config
            ).result()
            data_loaded = True

    # --- WEBHOOK TRIGGER ---
    if data_loaded:
        try:
            api_url = os.getenv("BACKEND_API_URL")
            if api_url:
                refresh_endpoint = f"{api_url}/api/admin/refresh"
                requests.post(refresh_endpoint, timeout=(3.05, 5))
                print(f"Webhook accepted for backend: {api_url}")
            else:
                print("Webhook skipped: BACKEND_API_URL not set.")
        except requests.exceptions.ReadTimeout:
            print("Webhook sent; Backend is processing.")
        except Exception as e:
            print(f"Warning: Failed to trigger backend refresh: {e}")

    return f"Successfully synced {len(actual_dates)} days.", 200