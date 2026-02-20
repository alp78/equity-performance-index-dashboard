"""
Cloud Function — Daily Price Sync (Multi-Index)
================================================
Resilient sync with per-symbol gap detection and 
market-specific holiday/calendar awareness.
"""

import functions_framework
from google.cloud import bigquery, storage
from os import getenv
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import exchange_calendars as xcals
import json
import time
import requests
import logging
from io import StringIO

logger = logging.getLogger("sync")
logger.setLevel(logging.INFO)

PROJECT_ID  = getenv("PROJECT_ID")
DATASET_ID  = getenv("DATASET_ID")
BUCKET_NAME = getenv("BUCKET_NAME")

INDICES = {
    "stoxx50": {
        "table_id": getenv("STOXX50_TABLE_ID", "stoxx50_prices"),
        "sync_after_utc": 17,
        "cal": "XFRA",
    },
    "sp500": {
        "table_id": getenv("SP500_TABLE_ID", "sp500_prices"),
        "sync_after_utc": 21,
        "cal": "XNYS",
    },
    "ftse100": {
        "table_id": getenv("FTSE100_TABLE_ID", "ftse100_prices"),
        "sync_after_utc": 17,
        "cal": "XLON",
    },
    "nikkei225": {
        "table_id": getenv("NIKKEI225_TABLE_ID", "nikkei225_prices"),
        "sync_after_utc": 7,
        "cal": "XTKS",
    },
    "csi300": {
        "table_id": getenv("CSI300_TABLE_ID", "csi300_prices"),
        "sync_after_utc": 8,
        "cal": "XSHG",
    },
    "nifty50": {
        "table_id": getenv("NIFTY50_TABLE_ID", "nifty50_prices"),
        "sync_after_utc": 11,
        "cal": "XBOM",  # Bombay Stock Exchange handles the Indian trading calendar
    },
}

MAX_RETRIES = 3
RETRY_DELAY_SEC = 30

EXCHANGE_SUFFIXES = {
    '.DE', '.PA', '.AS', '.BR', '.MI', '.MC', '.HE', '.VI',
    '.L',  '.SW', '.ST', '.CO', '.OL',
    '.T',  '.HK', '.SI', '.AX', '.NZ', '.SA', '.MX',
    '.TO', '.V',  '.NS', '.BO', '.SS', '.SZ',
}


def to_yf_ticker(db_ticker):
    for suffix in EXCHANGE_SUFFIXES:
        if db_ticker.upper().endswith(suffix.upper()):
            return db_ticker
    return db_ticker.replace('.', '-')

def get_full_table_ref(short_table_id):
    return f"{PROJECT_ID}.{DATASET_ID}.{short_table_id}"

# ============================================================================
# CALENDAR HELPERS
# ============================================================================

def get_expected_last_trading_day(cal, d):
    """Most recent valid trading session on or before date d."""
    while not cal.is_session(d.strftime('%Y-%m-%d')):
        d -= timedelta(days=1)
    return d

def get_valid_trading_days(cal, start_date, end_date):
    """Generate all exact trading days between start and end (inclusive)."""
    days = []
    d = start_date
    while d <= end_date:
        if cal.is_session(d.strftime('%Y-%m-%d')):
            days.append(d)
        d += timedelta(days=1)
    return days

# ============================================================================
# STEP 1 — PER-SYMBOL GAP DETECTION
# ============================================================================

def get_db_state_detailed(bq_client, table_ref, effective_today, cal):
    meta_query = f"""
        SELECT 
            symbol,
            MAX(name) as name,
            MAX(sector) as sector,
            MAX(industry) as industry,
            MAX(trade_date) as last_date
        FROM `{table_ref}`
        GROUP BY symbol
    """
    meta_rows = list(bq_client.query(meta_query).result())

    name_map, sector_map, industry_map = {}, {}, {}
    symbol_last_dates = {}
    global_last_date = datetime(2023, 1, 1).date()

    for row in meta_rows:
        sym = row.symbol
        name_map[sym] = row.name or sym
        if row.sector and row.sector not in ('N/A', '0', ''):
            sector_map[sym] = row.sector
        if row.industry and row.industry not in ('N/A', '0', ''):
            industry_map[sym] = row.industry
        if row.last_date:
            d = row.last_date.date() if hasattr(row.last_date, 'date') else row.last_date
            symbol_last_dates[sym] = d
            if d > global_last_date:
                global_last_date = d

    tickers = list(name_map.keys())
    if not tickers:
        return tickers, global_last_date, name_map, sector_map, industry_map, {}, set()

    lookback_start = get_expected_last_trading_day(cal, effective_today - timedelta(days=16))
    gap_query = f"""
        SELECT symbol, CAST(trade_date AS DATE) as td
        FROM `{table_ref}`
        WHERE trade_date >= '{lookback_start.strftime('%Y-%m-%d')}'
    """
    gap_rows = list(bq_client.query(gap_query).result())

    existing = set()
    for row in gap_rows:
        d = row.td.date() if hasattr(row.td, 'date') else row.td
        existing.add((row.symbol, d))

    expected_days = get_valid_trading_days(cal, lookback_start, get_expected_last_trading_day(cal, effective_today))

    symbol_missing_dates = {}
    all_gap_dates = set()
    for sym in tickers:
        missing = [d for d in expected_days if (sym, d) not in existing]
        if missing:
            symbol_missing_dates[sym] = missing
            all_gap_dates.update(missing)

    if symbol_missing_dates:
        total_gaps = sum(len(v) for v in symbol_missing_dates.values())
        logger.info(f"  Gap scan: {len(symbol_missing_dates)} symbols with {total_gaps} missing date-slots")

    return tickers, global_last_date, name_map, sector_map, industry_map, symbol_last_dates, symbol_missing_dates

# ============================================================================
# STEP 2 — DOWNLOAD & TRANSFORM
# ============================================================================

def download_and_transform(tickers, name_map, sector_map, industry_map,
                           start_date, end_date, symbol_last_dates):
    if not tickers:
        return []

    yf_map = {t: to_yf_ticker(t) for t in tickers}
    yf_tickers = list(yf_map.values())

    missing_meta = [t for t in tickers if t not in sector_map or t not in industry_map]
    if missing_meta:
        logger.info(f"  Fetching metadata for {len(missing_meta)} tickers...")
        for ticker in missing_meta[:50]:
            try:
                info = yf.Ticker(yf_map[ticker]).info
                s, i = info.get('sector', 'N/A'), info.get('industry', 'N/A')
                if s and s != 'N/A': sector_map[ticker] = s
                if i and i != 'N/A': industry_map[ticker] = i
            except:
                pass
            time.sleep(0.1)

    start_str = start_date.strftime('%Y-%m-%d')
    end_str = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')

    logger.info(f"  Bulk download: {len(yf_tickers)} tickers ({start_date} → {end_date})...")
    data = _try_download(yf_tickers, start_str, end_str)

    bulk_symbols = set()
    if data is not None and not data.empty:
        is_multi = isinstance(data.columns, pd.MultiIndex)
        if is_multi:
            bulk_symbols = set(data.columns.get_level_values(0).unique())

    if len(bulk_symbols) < len(yf_tickers) * 0.3 and len(yf_tickers) > 10:
        logger.warning(f"  Bulk got {len(bulk_symbols)}/{len(yf_tickers)} tickers. Falling back to batches...")
        data = _batch_download(yf_tickers, start_str, end_str, batch_size=30)

    if data is None or data.empty:
        return []

    return _extract_records(data, tickers, yf_map, name_map, sector_map, industry_map, symbol_last_dates)


def _try_download(yf_tickers, start_str, end_str):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = yf.download(
                yf_tickers, start=start_str, end=end_str,
                group_by='ticker', auto_adjust=False, threads=False,
            )
            if data is not None and not data.empty:
                logger.info(f"  Download OK (attempt {attempt}): {data.shape}")
                return data
        except Exception as e:
            logger.warning(f"  Download failed (attempt {attempt}): {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    return None


def _batch_download(yf_tickers, start_str, end_str, batch_size=30):
    all_frames = []
    for i in range(0, len(yf_tickers), batch_size):
        batch = yf_tickers[i:i + batch_size]
        logger.info(f"  Batch {i // batch_size + 1}: {len(batch)} tickers...")
        try:
            data = yf.download(
                batch, start=start_str, end=end_str,
                group_by='ticker', auto_adjust=False, threads=True,
            )
            if data is not None and not data.empty:
                all_frames.append(data)
                logger.info(f"    Got {data.shape}")
        except Exception as e:
            logger.warning(f"    Batch failed: {e}")
        time.sleep(5)

    if not all_frames:
        return None

    try:
        merged = pd.concat(all_frames, axis=1)
        merged = merged.loc[:, ~merged.columns.duplicated()]
        logger.info(f"  Merged {len(all_frames)} batches: {merged.shape}")
        return merged
    except Exception as e:
        logger.warning(f"  Merge failed: {e}")
        return all_frames[0] if all_frames else None


def _extract_records(data, tickers, yf_map, name_map, sector_map, industry_map, symbol_last_dates):
    records = []
    failed_tickers = []
    is_multi = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    for date_str in actual_dates:
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        for original_ticker in tickers:
            sym_last = symbol_last_dates.get(original_ticker)
            if sym_last and current_date <= sym_last:
                continue

            try:
                yf_ticker = yf_map[original_ticker]
                if is_multi:
                    if yf_ticker not in data.columns.get_level_values(0):
                        if original_ticker not in failed_tickers:
                            failed_tickers.append(original_ticker)
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    ticker_data = data.loc[date_str]

                close_val = ticker_data.get('Close')
                if close_val is None or pd.isna(close_val):
                    continue

                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(close_val)

                records.append({
                    "symbol":      original_ticker,
                    "name":        name_map.get(original_ticker, original_ticker),
                    "trade_date":  date_str,
                    "open_price":  open_p,
                    "close_price": float(close_val),
                    "high_price":  float(ticker_data['High']),
                    "low_price":   float(ticker_data['Low']),
                    "volume":      int(ticker_data['Volume']),
                    "sector":      sector_map.get(original_ticker, 'N/A'),
                    "industry":    industry_map.get(original_ticker, 'N/A'),
                })
            except Exception:
                if original_ticker not in failed_tickers:
                    failed_tickers.append(original_ticker)
                continue

    if failed_tickers:
        logger.warning(f"  {len(failed_tickers)} tickers had no data: {failed_tickers[:15]}")

    updated_symbols = set(r["symbol"] for r in records)
    logger.info(f"  Result: {len(records)} records for {len(updated_symbols)} symbols")
    return records

# ============================================================================
# STEP 3 — LOAD TO GCS & BIGQUERY
# ============================================================================

def load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, gcs_prefix, effective_today):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob_path = f"{gcs_prefix}/{effective_today.strftime('%Y/%m/%d')}/prices.json"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(json.dumps(records), content_type='application/json')

    ndjson = "\n".join([json.dumps(r) for r in records])
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    bq_client.load_table_from_file(StringIO(ndjson), table_ref, job_config=job_config).result()
    logger.info(f"  BigQuery: {len(records)} records → {table_ref}")

# ============================================================================
# SYNC LOGIC
# ============================================================================

def sync_index(bq_client, storage_client, index_key, config, now_utc):
    table_ref = get_full_table_ref(config["table_id"])
    cal_name = config.get("cal", "XNYS")
    cal = xcals.get_calendar(cal_name)

    effective_today = now_utc.date()
    sync_hour = config.get("sync_after_utc", 0)
    
    if now_utc.hour < sync_hour:
        effective_today -= timedelta(days=1)
        logger.info(f"[{index_key.upper()}] Pre-close sync. Adjusting effective date to {effective_today}")
    else:
        logger.info(f"[{index_key.upper()}] Post-close sync. Effective date is {effective_today}")

    logger.info(f"\n[{index_key.upper()}] Checking {table_ref} (Calendar: {cal_name})...")

    try:
        tickers, global_last, name_map, sector_map, industry_map, symbol_last_dates, symbol_missing_dates = \
            get_db_state_detailed(bq_client, table_ref, effective_today, cal)
        logger.info(f"  {len(tickers)} tickers, global last: {global_last}")
    except Exception as e:
        logger.critical(f"  DB error for {index_key}: {e}")
        return 0, f"Error: {e}"

    if not tickers:
        return 0, "No tickers"

    expected = get_expected_last_trading_day(cal, effective_today)

    trailing_behind = {s: d for s, d in symbol_last_dates.items() if d < expected}
    all_gap_symbols = set(symbol_missing_dates.keys()) | set(trailing_behind.keys())

    if not all_gap_symbols:
        logger.info(f"  All {len(tickers)} symbols up to date ({expected}), no interior gaps")
        return 0, "Up to date"

    earliest_needed = expected
    if trailing_behind:
        earliest_trailing = min(trailing_behind.values())
        earliest_needed = min(earliest_needed, get_expected_last_trading_day(cal, earliest_trailing + timedelta(days=1)))
    if symbol_missing_dates:
        all_missing = [d for dates in symbol_missing_dates.values() for d in dates]
        if all_missing:
            earliest_needed = min(earliest_needed, min(all_missing))

    logger.info(f"  {len(all_gap_symbols)} symbols need data. "
                f"Trailing: {len(trailing_behind)}, Interior gaps: {len(symbol_missing_dates)}. "
                f"Fetching from {earliest_needed}")

    symbol_effective_start = {}
    for sym in tickers:
        missing = symbol_missing_dates.get(sym, [])
        trailing = symbol_last_dates.get(sym, datetime(2023, 1, 1).date())
        if missing:
            sym_earliest = min(missing) - timedelta(days=1)
            symbol_effective_start[sym] = sym_earliest
        elif sym in trailing_behind:
            symbol_effective_start[sym] = trailing
        else:
            symbol_effective_start[sym] = expected

    records = download_and_transform(
        tickers, name_map, sector_map, industry_map,
        earliest_needed, effective_today, symbol_effective_start
    )

    if not records:
        sample = list(all_gap_symbols)[:10]
        logger.critical(f"  No data for {index_key}. Gap symbols: {sample}")
        return 0, f"No data ({len(all_gap_symbols)} gaps)"

    try:
        load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, index_key, effective_today)

        updated = set(r["symbol"] for r in records)
        still_missing = all_gap_symbols - updated
        if still_missing:
            logger.warning(f"  Still missing: {list(still_missing)[:10]}")

        return len(records), f"{len(records)} records ({len(updated)} symbols)"
    except Exception as e:
        logger.critical(f"  Load failed: {e}")
        return 0, f"Load error: {e}"

# ============================================================================
# ENTRY POINT
# ============================================================================

@functions_framework.http
def sync_stocks(request):
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    now_utc = datetime.utcnow()

    target_index = "all"
    try:
        body = request.get_json(silent=True) or {}
        target_index = body.get("index", "all").lower()
    except:
        pass

    if target_index in INDICES:
        indices_to_sync = {target_index: INDICES[target_index]}
    else:
        indices_to_sync = INDICES

    logger.info(f"--- Sync (target: {target_index}, UTC: {now_utc.isoformat()}) ---")

    total_records = 0
    results = {}
    synced_indices = []

    for index_key, config in indices_to_sync.items():
        count, status = sync_index(bq_client, storage_client, index_key, config, now_utc)
        total_records += count
        results[index_key] = status
        if count > 0:
            synced_indices.append(index_key)

    if synced_indices:
        api_url = getenv("BACKEND_API_URL")
        if api_url:
            for idx in synced_indices:
                try:
                    requests.post(f"{api_url}/api/admin/refresh/{idx}", timeout=(3.05, 5))
                    logger.info(f"  Webhook → {idx}")
                except requests.exceptions.ReadTimeout:
                    pass
                except Exception as e:
                    logger.warning(f"  Webhook error {idx}: {e}")

    summary = f"Done (target: {target_index}). {total_records} records. {json.dumps(results)}"
    logger.info(f"\n--- {summary} ---")
    return summary, 200