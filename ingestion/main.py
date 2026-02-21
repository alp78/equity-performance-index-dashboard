# Cloud Function: syncs stock and index price data from yfinance to BigQuery
# with per-symbol gap detection and exchange-calendar-aware scheduling

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

# --- STOCK INDEX CONFIGS ---

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
        "cal": "XBOM",
    },
}

# --- INDEX-LEVEL PRICE TICKERS ---

INDEX_PRICE_TABLE = getenv("INDEX_PRICE_TABLE_ID", "index_prices")

INDEX_TICKERS = {
    "^GSPC":      {"name": "S&P 500",       "currency": "USD", "exchange": "SNP",  "cal": "XNYS"},
    "^STOXX50E":  {"name": "EURO STOXX 50", "currency": "EUR", "exchange": "XETR", "cal": "XFRA"},
    "^FTSE":      {"name": "FTSE 100",      "currency": "GBP", "exchange": "LSE",  "cal": "XLON"},
    "^N225":      {"name": "Nikkei 225",    "currency": "JPY", "exchange": "TKS",  "cal": "XTKS"},
    "000300.SS":  {"name": "CSI 300",       "currency": "CNY", "exchange": "SHG",  "cal": "XSHG"},
    "^NSEI":      {"name": "Nifty 50",      "currency": "INR", "exchange": "NSE",  "cal": "XBOM"},
}

MAX_RETRIES = 3
RETRY_DELAY_SEC = 30

# suffixes that indicate an exchange-qualified ticker (should not be normalized)
EXCHANGE_SUFFIXES = {
    '.DE', '.PA', '.AS', '.BR', '.MI', '.MC', '.HE', '.VI',
    '.L',  '.SW', '.ST', '.CO', '.OL',
    '.T',  '.HK', '.SI', '.AX', '.NZ', '.SA', '.MX',
    '.TO', '.V',  '.NS', '.BO', '.SS', '.SZ',
}


def to_yf_ticker(db_ticker):
    # keep exchange-suffixed tickers as-is; replace dots with dashes otherwise
    for suffix in EXCHANGE_SUFFIXES:
        if db_ticker.upper().endswith(suffix.upper()):
            return db_ticker
    return db_ticker.replace('.', '-')

def get_full_table_ref(short_table_id):
    return f"{PROJECT_ID}.{DATASET_ID}.{short_table_id}"

# --- CALENDAR HELPERS ---

def get_expected_last_trading_day(cal, d):
    """most recent valid trading session on or before date d."""
    while not cal.is_session(d.strftime('%Y-%m-%d')):
        d -= timedelta(days=1)
    return d

def get_valid_trading_days(cal, start_date, end_date):
    """all trading days between start and end (inclusive)."""
    days = []
    d = start_date
    while d <= end_date:
        if cal.is_session(d.strftime('%Y-%m-%d')):
            days.append(d)
        d += timedelta(days=1)
    return days

# --- GAP DETECTION (STOCK PRICES) ---

def get_db_state_detailed(bq_client, table_ref, effective_today, cal):
    # fetch per-symbol metadata and latest trade dates
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

    # scan for interior gaps within a 16-day lookback window
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

    # compare expected vs existing to find missing date-slots per symbol
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

# --- DOWNLOAD AND TRANSFORM (STOCK PRICES) ---

def download_and_transform(tickers, name_map, sector_map, industry_map,
                           start_date, end_date, symbol_last_dates):
    if not tickers:
        return []

    yf_map = {t: to_yf_ticker(t) for t in tickers}
    yf_tickers = list(yf_map.values())

    # backfill missing sector/industry metadata from yfinance (capped at 50)
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

    # fall back to smaller batches if bulk captured <30% of tickers
    if len(bulk_symbols) < len(yf_tickers) * 0.3 and len(yf_tickers) > 10:
        logger.warning(f"  Bulk got {len(bulk_symbols)}/{len(yf_tickers)} tickers. Falling back to batches...")
        data = _batch_download(yf_tickers, start_str, end_str, batch_size=30)

    if data is None or data.empty:
        return []

    return _extract_records(data, tickers, yf_map, name_map, sector_map, industry_map, symbol_last_dates)


def _try_download(yf_tickers, start_str, end_str):
    """attempt bulk yfinance download with retries."""
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
    """download in smaller batches and merge results."""
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
    """convert downloaded DataFrame into list of BigQuery-ready dicts."""
    records = []
    failed_tickers = []
    is_multi = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    for date_str in actual_dates:
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        for original_ticker in tickers:
            # skip dates already covered in BigQuery
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

                # fall back to close if open is missing
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

# --- LOAD TO GCS AND BIGQUERY ---

def load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, gcs_prefix, effective_today):
    # archive JSON snapshot to GCS, then append NDJSON to BigQuery
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

# --- SYNC STOCK PRICES ---

def sync_index(bq_client, storage_client, index_key, config, now_utc):
    table_ref = get_full_table_ref(config["table_id"])
    cal_name = config.get("cal", "XNYS")
    cal = xcals.get_calendar(cal_name)

    # shift effective date back if market hasn't closed yet
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

    # identify symbols that are trailing (behind latest session) or have interior gaps
    trailing_behind = {s: d for s, d in symbol_last_dates.items() if d < expected}
    all_gap_symbols = set(symbol_missing_dates.keys()) | set(trailing_behind.keys())

    if not all_gap_symbols:
        logger.info(f"  All {len(tickers)} symbols up to date ({expected}), no interior gaps")
        return 0, f"0 rows ({len(tickers)} symbols up to date)"

    # determine the earliest date we need to fetch across all gap types
    earliest_needed = expected
    if trailing_behind:
        earliest_trailing = min(trailing_behind.values())
        earliest_needed = min(earliest_needed, get_expected_last_trading_day(cal, earliest_trailing + timedelta(days=1)))
    if symbol_missing_dates:
        all_missing = [d for dates in symbol_missing_dates.values() for d in dates]
        if all_missing:
            earliest_needed = min(earliest_needed, min(all_missing))

    gap_tickers = list(all_gap_symbols)
    logger.info(f"  {len(gap_tickers)}/{len(tickers)} symbols need data. "
                f"Trailing: {len(trailing_behind)}, Interior gaps: {len(symbol_missing_dates)}. "
                f"Fetching from {earliest_needed}")

    # per-symbol effective start date to avoid re-inserting existing rows
    symbol_effective_start = {}
    for sym in gap_tickers:
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
        gap_tickers, name_map, sector_map, industry_map,
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

        return len(records), f"{len(records)} rows written ({len(updated)}/{len(tickers)} symbols updated)"
    except Exception as e:
        logger.critical(f"  Load failed: {e}")
        return 0, f"Load error: {e}"


# --- SYNC INDEX PRICES ---

def _get_index_prices_state(bq_client, table_ref, now_utc):
    """per-symbol gap detection for the index_prices table, each using its own exchange calendar."""
    meta_query = f"""
        SELECT symbol,
               MAX(name) as name,
               MAX(currency) as currency,
               MAX(exchange) as exchange,
               MAX(trade_date) as last_date
        FROM `{table_ref}`
        GROUP BY symbol
    """
    meta_rows = list(bq_client.query(meta_query).result())

    symbol_meta = {}
    symbol_last_dates = {}

    for row in meta_rows:
        sym = row.symbol
        symbol_meta[sym] = {
            "name": row.name or sym,
            "currency": row.currency or "N/A",
            "exchange": row.exchange or "N/A",
        }
        if row.last_date:
            d = row.last_date.date() if hasattr(row.last_date, 'date') else row.last_date
            symbol_last_dates[sym] = d

    # interior gap detection per symbol using its own calendar
    symbol_missing_dates = {}
    effective_today = now_utc.date()

    for sym, cfg in INDEX_TICKERS.items():
        if sym not in symbol_last_dates:
            continue

        cal = xcals.get_calendar(cfg["cal"])
        lookback_start = get_expected_last_trading_day(cal, effective_today - timedelta(days=16))
        expected_days = get_valid_trading_days(cal, lookback_start, get_expected_last_trading_day(cal, effective_today))

        gap_query = f"""
            SELECT CAST(trade_date AS DATE) as td
            FROM `{table_ref}`
            WHERE symbol = '{sym}' AND trade_date >= '{lookback_start.strftime('%Y-%m-%d')}'
        """
        gap_rows = list(bq_client.query(gap_query).result())
        existing_dates = {
            (row.td.date() if hasattr(row.td, 'date') else row.td) for row in gap_rows
        }

        missing = [d for d in expected_days if d not in existing_dates]
        if missing:
            symbol_missing_dates[sym] = missing

    return symbol_meta, symbol_last_dates, symbol_missing_dates


def sync_index_prices(bq_client, storage_client, now_utc):
    """sync the index_prices table with per-symbol gap detection."""
    table_ref = get_full_table_ref(INDEX_PRICE_TABLE)
    logger.info(f"\n[INDEX PRICES] Checking {table_ref}...")

    try:
        symbol_meta, symbol_last_dates, symbol_missing_dates = \
            _get_index_prices_state(bq_client, table_ref, now_utc)
        logger.info(f"  {len(symbol_last_dates)} index tickers in DB")
    except Exception as e:
        logger.critical(f"  DB error for index_prices: {e}")
        return 0, f"Error: {e}"

    effective_today = now_utc.date()
    symbols_to_update = {}

    # determine which index tickers are trailing or have gaps
    for sym, cfg in INDEX_TICKERS.items():
        cal = xcals.get_calendar(cfg["cal"])
        expected = get_expected_last_trading_day(cal, effective_today)

        trailing = sym in symbol_last_dates and symbol_last_dates[sym] < expected
        has_gaps = sym in symbol_missing_dates

        if trailing or has_gaps:
            earliest = expected
            if trailing:
                earliest = min(earliest, symbol_last_dates[sym] + timedelta(days=1))
            if has_gaps:
                earliest = min(earliest, min(symbol_missing_dates[sym]))

            symbols_to_update[sym] = {
                "earliest": earliest,
                "effective_start": (min(symbol_missing_dates[sym]) - timedelta(days=1)) if has_gaps
                                   else symbol_last_dates.get(sym, datetime(2023, 1, 1).date()),
                **cfg,
            }

    if not symbols_to_update:
        logger.info(f"  All {len(INDEX_TICKERS)} index tickers up to date")
        return 0, f"0 rows ({len(INDEX_TICKERS)} tickers up to date)"

    logger.info(f"  {len(symbols_to_update)} index tickers need data: {list(symbols_to_update.keys())}")

    all_syms = list(symbols_to_update.keys())
    earliest_overall = min(v["earliest"] for v in symbols_to_update.values())
    start_str = earliest_overall.strftime('%Y-%m-%d')
    end_str = (effective_today + timedelta(days=1)).strftime('%Y-%m-%d')

    logger.info(f"  Downloading {len(all_syms)} index tickers ({earliest_overall} → {effective_today})...")

    data = _try_download(all_syms, start_str, end_str)

    # fall back to individual downloads if bulk fails
    if data is None or data.empty:
        logger.warning(f"  Bulk failed, trying individual downloads...")
        all_frames = []
        for sym in all_syms:
            try:
                d = yf.download(sym, start=start_str, end=end_str, auto_adjust=False)
                if d is not None and not d.empty:
                    # reshape single-ticker columns into MultiIndex to match bulk format
                    d.columns = pd.MultiIndex.from_product([[sym], d.columns])
                    all_frames.append(d)
                    logger.info(f"    {sym}: {len(d)} rows")
            except Exception as e:
                logger.warning(f"    {sym} failed: {e}")
            time.sleep(1)
        if all_frames:
            data = pd.concat(all_frames, axis=1)
        else:
            logger.critical(f"  No data for any index ticker")
            return 0, "No data"

    # build index_prices records from downloaded data
    records = []
    is_multi = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    for date_str in actual_dates:
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        for sym, update_info in symbols_to_update.items():
            if current_date <= update_info["effective_start"]:
                continue

            try:
                if is_multi:
                    if sym not in data.columns.get_level_values(0):
                        continue
                    ticker_data = data[sym].loc[date_str]
                elif len(all_syms) == 1:
                    ticker_data = data.loc[date_str]
                else:
                    continue

                close_val = ticker_data.get('Close')
                if close_val is None or pd.isna(close_val):
                    continue

                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(close_val)
                vol = ticker_data.get('Volume', 0)

                meta = symbol_meta.get(sym, {})
                records.append({
                    "symbol":      sym,
                    "trade_date":  date_str,
                    "open_price":  open_p,
                    "high_price":  float(ticker_data['High']),
                    "low_price":   float(ticker_data['Low']),
                    "close_price": float(close_val),
                    "volume":      int(vol) if pd.notna(vol) else 0,
                    "name":        meta.get("name", update_info["name"]),
                    "currency":    meta.get("currency", update_info["currency"]),
                    "exchange":    meta.get("exchange", update_info["exchange"]),
                })
            except Exception as e:
                logger.warning(f"  Index parse error {sym}/{date_str}: {e}")
                continue

    if not records:
        logger.warning(f"  No new index price records")
        return 0, "No new data"

    updated_symbols = set(r["symbol"] for r in records)
    logger.info(f"  Index prices: {len(records)} records for {len(updated_symbols)} tickers")

    try:
        load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, "index_prices", effective_today)
        return len(records), f"{len(records)} rows written ({len(updated_symbols)}/{len(INDEX_TICKERS)} tickers updated)"
    except Exception as e:
        logger.critical(f"  Index prices load failed: {e}")
        return 0, f"Load error: {e}"


# --- ENTRY POINT ---

@functions_framework.http
def sync_stocks(request):
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    now_utc = datetime.utcnow()

    # allow targeting a single index via request body {"index": "sp500"}
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

    # sync per-stock prices for each index
    for index_key, config in indices_to_sync.items():
        count, status = sync_index(bq_client, storage_client, index_key, config, now_utc)
        total_records += count
        results[index_key] = status
        if count > 0:
            synced_indices.append(index_key)

    # sync index-level prices (runs on every invocation)
    try:
        idx_count, idx_status = sync_index_prices(bq_client, storage_client, now_utc)
        total_records += idx_count
        results["index_prices"] = idx_status
    except Exception as e:
        logger.critical(f"  Index prices sync error: {e}")
        results["index_prices"] = f"Error: {e}"

    # notify backend to refresh caches for updated indices
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

        if idx_count > 0:
            try:
                requests.post(f"{api_url}/api/admin/refresh/index_prices", timeout=(3.05, 5))
                logger.info(f"  Webhook → index_prices")
            except requests.exceptions.ReadTimeout:
                pass
            except Exception as e:
                logger.warning(f"  Webhook error index_prices: {e}")

    lines = [f"Sync complete (target: {target_index}) - {total_records} total rows written"]
    for key, status in results.items():
        lines.append(f"  {key}: {status}")
    summary = "\n".join(lines)
    logger.info(f"\n--- {summary} ---")
    return summary, 200
