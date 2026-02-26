"""
═══════════════════════════════════════════════════════════════════════════
 backfill_index_prices.py — Add ^BVSP and ^AXJO to index_prices table
═══════════════════════════════════════════════════════════════════════════
 Downloads historical index-level OHLCV data from Yahoo Finance and
 appends it to the existing BigQuery index_prices table.  The series
 start date is aligned to the earliest date already in the table so
 all indices share the same origin.

 Uses yf.Ticker().history() (reliable in yfinance 1.1.0, no custom
 session needed).

 Usage:
   pip install yfinance pandas google-cloud-bigquery
   python scripts/backfill_index_prices.py
═══════════════════════════════════════════════════════════════════════════
"""

import yfinance as yf
import pandas as pd
from google.cloud import bigquery
from datetime import datetime
import json
import time

# ─── Config ───────────────────────────────────────────────────────────
PROJECT_ID = "esg-analytics-poc"
DATASET_ID = "stock_exchange"
TABLE_ID = "index_prices"
TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

INDICES_TO_ADD = {
    "^BVSP": {
        "name": "Ibovespa",
        "currency": "BRL",
        "exchange": "BR",
    },
    "^AXJO": {
        "name": "S&P/ASX 200",
        "currency": "AUD",
        "exchange": "AU",
    },
}

RETRY_LIMIT = 3
RETRY_DELAY = 5


def get_origin_date(bq_client):
    """Query BQ for the earliest trade_date in index_prices."""
    query = f"SELECT MIN(trade_date) AS min_date FROM `{TABLE_REF}`"
    result = bq_client.query(query).result()
    for row in result:
        d = row.min_date
        if d is None:
            raise RuntimeError("index_prices table is empty — cannot determine origin date")
        if isinstance(d, str):
            return d
        return d.strftime("%Y-%m-%d")


def check_existing(bq_client, symbol):
    """Check if this symbol already exists in the table."""
    query = f"""
        SELECT COUNT(*) AS cnt, MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
        FROM `{TABLE_REF}`
        WHERE symbol = '{symbol}'
    """
    for row in bq_client.query(query).result():
        return row.cnt, row.min_d, row.max_d
    return 0, None, None


def download_index(symbol, start_date, end_date):
    """Download OHLCV for an index ticker using yf.Ticker().history()."""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            t = yf.Ticker(symbol)
            df = t.history(start=start_date, end=end_date, auto_adjust=True)
            if df is not None and not df.empty:
                print(f"  {symbol}: {len(df)} rows  ({df.index[0].strftime('%Y-%m-%d')} → {df.index[-1].strftime('%Y-%m-%d')})")
                return df
            print(f"  {symbol}: empty result (attempt {attempt})")
        except Exception as e:
            print(f"  {symbol}: error (attempt {attempt}): {e}")
        if attempt < RETRY_LIMIT:
            time.sleep(RETRY_DELAY)

    print(f"  {symbol}: FAILED after {RETRY_LIMIT} attempts")
    return pd.DataFrame()


def to_records(df, symbol, meta):
    """Convert DataFrame to list of dicts matching index_prices schema."""
    records = []
    for date_idx, row in df.iterrows():
        try:
            close_val = row.get("Close")
            if close_val is None or pd.isna(close_val):
                continue
            raw_open = row.get("Open")
            open_p = float(raw_open) if pd.notna(raw_open) else float(close_val)
            vol = row.get("Volume", 0)

            records.append({
                "symbol":      symbol,
                "trade_date":  date_idx.strftime("%Y-%m-%d"),
                "open_price":  round(open_p, 4),
                "high_price":  round(float(row["High"]), 4),
                "low_price":   round(float(row["Low"]), 4),
                "close_price": round(float(close_val), 4),
                "volume":      int(vol) if pd.notna(vol) else 0,
                "name":        meta["name"],
                "currency":    meta["currency"],
                "exchange":    meta["exchange"],
            })
        except Exception:
            continue
    return records


def load_to_bq(bq_client, records):
    """Append NDJSON records to BigQuery index_prices table."""
    from io import StringIO

    ndjson = "\n".join(json.dumps(r) for r in records)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = bq_client.load_table_from_file(StringIO(ndjson), TABLE_REF, job_config=job_config)
    job.result()
    print(f"  Loaded {len(records)} records → {TABLE_REF}")


def main():
    print("═══ Backfill index_prices: ^BVSP + ^AXJO ═══\n")

    bq_client = bigquery.Client(project=PROJECT_ID)

    # Step 1: Find origin date
    origin = get_origin_date(bq_client)
    end_date = datetime.now().strftime("%Y-%m-%d")
    print(f"  Table origin date : {origin}")
    print(f"  Download range    : {origin} → {end_date}\n")

    total_records = 0

    for symbol, meta in INDICES_TO_ADD.items():
        print(f"── {symbol} ({meta['name']}) ──")

        # Check if already present
        cnt, min_d, max_d = check_existing(bq_client, symbol)
        if cnt > 0:
            print(f"  Already in table: {cnt} rows ({min_d} → {max_d})")
            print(f"  Skipping — delete existing rows first if you want to reload.\n")
            continue

        # Download
        df = download_index(symbol, origin, end_date)
        if df.empty:
            print(f"  No data downloaded — skipping.\n")
            continue

        # Convert to records
        records = to_records(df, symbol, meta)
        print(f"  Records: {len(records)}")

        if not records:
            print(f"  No valid records — skipping.\n")
            continue

        # Load to BQ
        load_to_bq(bq_client, records)
        total_records += len(records)
        print()

    # Summary
    print(f"═══ Done ═══")
    print(f"  Total records loaded: {total_records:,}")


if __name__ == "__main__":
    main()
