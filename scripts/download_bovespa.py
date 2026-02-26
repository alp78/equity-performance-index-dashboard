"""
═══════════════════════════════════════════════════════════════════════════
 download_bovespa.py — Historical Ibovespa Constituent Data Download
═══════════════════════════════════════════════════════════════════════════
 Downloads ~10 years of daily OHLCV data for Ibovespa constituents from
 Yahoo Finance. Uses yf.Ticker().history() for probing (reliable in
 yfinance 1.1.0) and yf.download() for batch fetching. NO custom session
 (yfinance 1.1.0 uses curl_cffi internally).

 Pipeline:
   1. PROBE     — test each candidate with .history(period='5d'),
                   check full date range, apply ticker substitutions
   2. METADATA  — fetch name/sector/industry via .info
   3. DOWNLOAD  — batch yf.download() with single-ticker fallback
   4. CSV       — write BQ-compatible output

 Output columns: symbol, trade_date, open_price, high_price, low_price,
                  close_price, volume, name, sector, industry

 Usage:
   pip install yfinance pandas
   python scripts/download_bovespa.py
═══════════════════════════════════════════════════════════════════════════
"""

import yfinance as yf
import pandas as pd
import time
from datetime import datetime
from pathlib import Path

# ─── Output Config ───────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "bovespa_historical.csv"
START_DATE = "2016-02-22"
END_DATE = datetime.now().strftime("%Y-%m-%d")
MIN_HISTORY_YEARS = 5

# ─── Download Tuning ─────────────────────────────────────────────────────
BATCH_SIZE = 10
RETRY_LIMIT = 3
RETRY_DELAY = 5
INTER_BATCH_DELAY = 3

# ─── Candidate tickers (.SA suffix) ──────────────────────────────────────
# Ibovespa constituents as of 2026. Probed against Yahoo Finance:
# - CPLE3 replaces CPLE6 (Yahoo only has ON shares for Copel)
# - NATU3 replaces NTCO3 (Yahoo still uses old Natura ticker)
# - Excluded: ELET3/6, EMBR3, CCRO3, JBSS3, BRFS3, MRFG3, AZUL4, CRFB3
#   (Yahoo Finance 404 — no data available under any ticker variant)
CANDIDATES = [
    # Financials
    "ITUB4.SA", "BBDC3.SA", "BBDC4.SA", "BBAS3.SA", "BPAC11.SA",
    "SANB11.SA", "ITSA4.SA", "B3SA3.SA", "BBSE3.SA", "CXSE3.SA",
    "PSSA3.SA", "IRBR3.SA", "BRAP4.SA",
    # Energy / Oil & Gas
    "PETR3.SA", "PETR4.SA", "PRIO3.SA", "RECV3.SA", "BRAV3.SA",
    "CSAN3.SA", "RAIZ4.SA", "VBBR3.SA", "UGPA3.SA",
    # Mining & Metals
    "VALE3.SA", "CSNA3.SA", "CMIN3.SA", "GGBR4.SA", "GOAU4.SA",
    "USIM5.SA", "BRKM5.SA",
    # Utilities (CPLE3 = Copel ON, replaces CPLE6 which Yahoo doesn't have)
    "EQTL3.SA", "CPFE3.SA", "CMIG4.SA", "ENGI11.SA", "EGIE3.SA",
    "ENEV3.SA", "CPLE3.SA", "TAEE11.SA", "AURE3.SA", "SBSP3.SA",
    "ISAE4.SA",
    # Consumer / Retail (NATU3 = old Natura ticker, replaces NTCO3)
    "ABEV3.SA", "LREN3.SA", "MGLU3.SA", "AZZA3.SA", "NATU3.SA",
    "VIVA3.SA", "CEAB3.SA", "RADL3.SA", "HYPE3.SA", "ASAI3.SA",
    "PCAR3.SA",
    # Healthcare
    "RDOR3.SA", "HAPV3.SA", "FLRY3.SA", "SMFT3.SA",
    # Industrials & Transportation
    "WEGE3.SA", "RAIL3.SA", "RENT3.SA", "VAMO3.SA", "POMO4.SA",
    # Real Estate
    "CYRE3.SA", "MRVE3.SA", "MULT3.SA", "ALOS3.SA", "IGTI11.SA",
    "CURY3.SA", "DIRR3.SA",
    # Telecom
    "VIVT3.SA", "TIMS3.SA",
    # Technology & Education
    "TOTS3.SA", "YDUQ3.SA", "COGN3.SA",
    # Food & Agriculture
    "BEEF3.SA", "SLCE3.SA", "SMTO3.SA",
    # Paper & Forestry
    "SUZB3.SA", "KLBN11.SA",
    # Travel & Leisure
    "CVCB3.SA",
    # Other
    "DXCO3.SA", "ALPA4.SA", "LWSA3.SA", "EZTC3.SA",
]


# ═════════════════════════════════════════════════════════════════════════
#  Phase 1: PROBE — verify each ticker with yf.Ticker().history()
# ═════════════════════════════════════════════════════════════════════════

def probe_ticker(yahoo_ticker):
    """Check ticker availability and date range using yf.Ticker().history().
    Returns (first_date, last_date, num_rows) or None."""
    for attempt in range(RETRY_LIMIT):
        try:
            t = yf.Ticker(yahoo_ticker)
            df = t.history(period="5d")
            if df is None or df.empty:
                if attempt < RETRY_LIMIT - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                return None
            # Check full range
            df_full = t.history(start="2010-01-01", end=END_DATE)
            if df_full is None or df_full.empty:
                return None
            first = df_full.index[0].strftime("%Y-%m-%d")
            last = df_full.index[-1].strftime("%Y-%m-%d")
            return first, last, len(df_full)
        except Exception:
            if attempt < RETRY_LIMIT - 1:
                time.sleep(RETRY_DELAY)
    return None


def validate_candidates(candidates):
    """Probe each candidate, return validated list and skip lists."""
    validated = []
    no_data = []
    too_short = []

    for i, ticker in enumerate(candidates, 1):
        result = probe_ticker(ticker)

        if result is None:
            no_data.append(ticker)
            print(f"  [{i:3d}/{len(candidates)}] {ticker:15s} NO DATA")
        else:
            first, last, rows = result
            first_dt = datetime.strptime(first, "%Y-%m-%d")
            years = (datetime.now() - first_dt).days / 365.25

            if years < MIN_HISTORY_YEARS:
                too_short.append((ticker, first, f"{years:.1f}yr"))
                print(f"  [{i:3d}/{len(candidates)}] {ticker:15s} TOO SHORT  from {first} ({years:.1f}yr)")
            else:
                validated.append(ticker)
                print(f"  [{i:3d}/{len(candidates)}] {ticker:15s} OK         from {first} ({years:.1f}yr, {rows} rows)")

    return validated, no_data, too_short


# ═════════════════════════════════════════════════════════════════════════
#  Phase 2: METADATA — fetch name/sector/industry
# ═════════════════════════════════════════════════════════════════════════

def fetch_metadata(ticker):
    """Fetch company name, sector, industry from yfinance .info (no session)."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or ticker.replace(".SA", "")
        sector = info.get("sector", "N/A")
        industry = info.get("industry", "N/A")
        return name, sector, industry
    except Exception:
        return ticker.replace(".SA", ""), "N/A", "N/A"


# ═════════════════════════════════════════════════════════════════════════
#  Phase 3: DOWNLOAD — batch yf.download() + single fallback
# ═════════════════════════════════════════════════════════════════════════

def download_single(ticker, start, end):
    """Download OHLCV for a single ticker with retries (no session)."""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            if attempt < RETRY_LIMIT:
                print(f"    Retry {attempt}/{RETRY_LIMIT} for {ticker}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"    FAILED {ticker} after {RETRY_LIMIT} attempts: {e}")
    return pd.DataFrame()


def download_batch(tickers, start, end):
    """Download OHLCV for a batch of tickers (no session)."""
    try:
        df = yf.download(
            tickers, start=start, end=end,
            group_by="ticker", auto_adjust=True, threads=True, progress=False,
        )
        return df
    except Exception as e:
        print(f"    Batch error: {e}")
        return pd.DataFrame()


def extract_records(df, ticker, name, sector, industry):
    """Convert a single-ticker DataFrame into list of record dicts."""
    records = []
    for date_idx, row in df.iterrows():
        try:
            close_val = row.get("Close")
            if close_val is None or pd.isna(close_val):
                continue
            open_val = row.get("Open")
            open_p = float(open_val) if pd.notna(open_val) else float(close_val)
            records.append({
                "symbol":      ticker,
                "trade_date":  date_idx.strftime("%Y-%m-%d"),
                "open_price":  round(open_p, 4),
                "high_price":  round(float(row["High"]), 4),
                "low_price":   round(float(row["Low"]), 4),
                "close_price": round(float(close_val), 4),
                "volume":      int(row.get("Volume", 0)),
                "name":        name,
                "sector":      sector,
                "industry":    industry,
            })
        except Exception:
            continue
    return records


# ═════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════

def main():
    print("═══ Bovespa Historical Data Download ═══")
    print(f"  Candidates    : {len(CANDIDATES)}")
    print(f"  Period        : {START_DATE} → {END_DATE}")
    print(f"  Min history   : {MIN_HISTORY_YEARS} years")
    print(f"  Output        : {OUTPUT_FILE}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Probe & validate ───────────────────────────────────────
    print("Phase 1: Probing tickers on Yahoo Finance...")
    validated, no_data, too_short = validate_candidates(CANDIDATES)
    print(f"\n  Validated : {len(validated)}")
    print(f"  No data   : {len(no_data)}")
    if no_data:
        print(f"              {', '.join(no_data)}")
    print(f"  Too short : {len(too_short)}")
    if too_short:
        for t, first, yrs in too_short:
            print(f"              {t} (from {first}, {yrs})")
    print()

    if not validated:
        print("ERROR: No valid tickers found.")
        return

    # ── Phase 2: Fetch metadata ─────────────────────────────────────────
    print("Phase 2: Fetching ticker metadata...")
    meta = {}
    for i, ticker in enumerate(validated, 1):
        name, sector, industry = fetch_metadata(ticker)
        meta[ticker] = (name, sector, industry)
        if i % 20 == 0:
            print(f"  Metadata: {i}/{len(validated)}")
    print(f"  Done: {len(meta)} tickers\n")

    # ── Phase 3: Full download ──────────────────────────────────────────
    print("Phase 3: Downloading full price history...")
    all_records = []
    failed_tickers = []
    batches = [validated[i:i + BATCH_SIZE] for i in range(0, len(validated), BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        print(f"  Batch {batch_num}/{len(batches)}: {batch[0]}...{batch[-1]} ({len(batch)} tickers)")

        if len(batch) == 1:
            ticker = batch[0]
            df = download_single(ticker, START_DATE, END_DATE)
            if not df.empty:
                name, sector, industry = meta[ticker]
                recs = extract_records(df, ticker, name, sector, industry)
                all_records.extend(recs)
                print(f"    {ticker}: {len(recs)} records")
            else:
                failed_tickers.append(ticker)
                print(f"    {ticker}: FAILED")
        else:
            df = download_batch(batch, START_DATE, END_DATE)
            for ticker in batch:
                try:
                    if df.empty:
                        raise ValueError("Empty batch")
                    if ticker in df.columns.get_level_values(0):
                        ticker_df = df[ticker].dropna(how="all")
                    else:
                        raise ValueError("Not in batch")
                    if ticker_df.empty:
                        raise ValueError("No rows")
                    name, sector, industry = meta[ticker]
                    recs = extract_records(ticker_df, ticker, name, sector, industry)
                    if recs:
                        all_records.extend(recs)
                        print(f"    {ticker}: {len(recs)} records")
                    else:
                        raise ValueError("No valid records")
                except Exception:
                    sdf = download_single(ticker, START_DATE, END_DATE)
                    if not sdf.empty:
                        name, sector, industry = meta[ticker]
                        recs = extract_records(sdf, ticker, name, sector, industry)
                        all_records.extend(recs)
                        print(f"    {ticker}: {len(recs)} records (fallback)")
                    else:
                        failed_tickers.append(ticker)
                        print(f"    {ticker}: FAILED")

        time.sleep(INTER_BATCH_DELAY)

    # ── Phase 4: Write CSV ──────────────────────────────────────────────
    print(f"\nPhase 4: Writing CSV...")
    result_df = pd.DataFrame(all_records)
    result_df.sort_values(["symbol", "trade_date"], inplace=True)
    result_df.to_csv(OUTPUT_FILE, index=False)

    # ── Summary ─────────────────────────────────────────────────────────
    n_syms = result_df["symbol"].nunique() if not result_df.empty else 0
    n_recs = len(result_df)
    dr = f"{result_df['trade_date'].min()} → {result_df['trade_date'].max()}" if not result_df.empty else "N/A"

    print(f"\n═══ Download Complete ═══")
    print(f"  Symbols downloaded : {n_syms}/{len(validated)} validated")
    print(f"  Total records      : {n_recs:,}")
    print(f"  Date range         : {dr}")
    print(f"  Output file        : {OUTPUT_FILE}")
    if OUTPUT_FILE.exists():
        print(f"  File size          : {OUTPUT_FILE.stat().st_size / (1024*1024):.1f} MB")

    if failed_tickers:
        print(f"\n  Download failures ({len(failed_tickers)}):")
        for t in failed_tickers:
            print(f"    - {t}")
    print()


if __name__ == "__main__":
    main()
