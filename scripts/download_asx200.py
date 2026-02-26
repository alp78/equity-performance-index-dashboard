"""
═══════════════════════════════════════════════════════════════════════════
 download_asx200.py — Historical S&P/ASX 200 Constituent Data Download
═══════════════════════════════════════════════════════════════════════════
 Downloads ~10 years of daily OHLCV data for ASX 200 constituents from
 Yahoo Finance. Uses yf.Ticker().history() for probing (reliable in
 yfinance 1.1.0) and yf.download() for batch fetching. NO custom session.

 Pipeline:
   1. PROBE     — test each candidate with .history(period='5d'),
                   check full date range, skip unavailable/too-short
   2. METADATA  — fetch name/sector/industry via .info
   3. DOWNLOAD  — batch yf.download() with single-ticker fallback
   4. CSV       — write BQ-compatible output

 Output columns: symbol, trade_date, open_price, high_price, low_price,
                  close_price, volume, name, sector, industry

 Usage:
   pip install yfinance pandas
   python scripts/download_asx200.py
═══════════════════════════════════════════════════════════════════════════
"""

import yfinance as yf
import pandas as pd
import time
from datetime import datetime
from pathlib import Path

# ─── Output Config ───────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "asx200_historical.csv"
START_DATE = "2016-02-22"
END_DATE = datetime.now().strftime("%Y-%m-%d")
MIN_HISTORY_YEARS = 5

# ─── Download Tuning ─────────────────────────────────────────────────────
BATCH_SIZE = 10
RETRY_LIMIT = 3
RETRY_DELAY = 5
INTER_BATCH_DELAY = 3

# ─── ASX 200 Candidate Tickers (.AX suffix for Yahoo Finance) ────────────
# Source: asx200list.com + MarketScreener (2025-2026), validated against Yahoo
# Excluded: ETFs/LICs, and 21 delisted companies (M&A 2021-2025):
#   NCM (→Newmont), OZL (→BHP), AWC (→S32), CSR (→Saint-Gobain),
#   OSH (→Santos), CWN (→Blackstone), CIM (→HOCHTIEF), BLD (→Seven Group),
#   ABC (→CRH), ABP (→ASK.AX), ALU (→Renesas), LNK (→MUFG),
#   VOC (→Macquarie), PDL (→PPT), GXY (→Arcadium), IPL (demerged),
#   DEG (→NST), SWM (→Seven Group), DHG/SVW/Z1P (Yahoo unavailable)
CANDIDATES = [
    # ── Financials ──────────────────────────────────────────────────────
    "CBA.AX",    # Commonwealth Bank
    "NAB.AX",    # National Australia Bank
    "WBC.AX",    # Westpac
    "ANZ.AX",    # ANZ Group
    "MQG.AX",    # Macquarie Group
    "QBE.AX",    # QBE Insurance
    "SUN.AX",    # Suncorp
    "IAG.AX",    # Insurance Australia Group
    "BOQ.AX",    # Bank of Queensland
    "BEN.AX",    # Bendigo & Adelaide Bank
    "AMP.AX",    # AMP
    "CGF.AX",    # Challenger
    "MFG.AX",    # Magellan Financial
    "IFL.AX",    # Insignia Financial
    "PPT.AX",    # Perpetual
    "HUB.AX",    # Hub24
    "NHF.AX",    # nib Holdings
    "MPL.AX",    # Medibank Private
    "ASX.AX",    # ASX Ltd (exchange operator)

    # ── Mining & Resources ──────────────────────────────────────────────
    "BHP.AX",    # BHP Group
    "RIO.AX",    # Rio Tinto
    "FMG.AX",    # Fortescue
    "S32.AX",    # South32
    "MIN.AX",    # Mineral Resources
    "NST.AX",    # Northern Star
    "EVN.AX",    # Evolution Mining
    "IGO.AX",    # IGO
    "ILU.AX",    # Iluka Resources
    "LYC.AX",    # Lynas Rare Earths
    "RRL.AX",    # Regis Resources
    "PLS.AX",    # Pilbara Minerals
    "NIC.AX",    # Nickel Industries
    "WHC.AX",    # Whitehaven Coal
    "YAL.AX",    # Yancoal Australia
    "BSL.AX",    # BlueScope Steel
    "SGM.AX",    # Sims

    # ── Energy ──────────────────────────────────────────────────────────
    "WDS.AX",    # Woodside Energy (was WPL)
    "STO.AX",    # Santos
    "BPT.AX",    # Beach Energy
    "ORG.AX",    # Origin Energy
    "AGL.AX",    # AGL Energy
    "APA.AX",    # APA Group (pipelines)
    "GNE.AX",    # Genesis Energy

    # ── Healthcare ──────────────────────────────────────────────────────
    "CSL.AX",    # CSL (biotech)
    "RMD.AX",    # ResMed
    "COH.AX",    # Cochlear
    "SHL.AX",    # Sonic Healthcare
    "RHC.AX",    # Ramsay Health Care
    "FPH.AX",    # Fisher & Paykel Healthcare
    "PME.AX",    # Pro Medicus
    "HLS.AX",    # Healius
    "EBO.AX",    # Ebos Group
    "PNV.AX",    # PolyNovo
    "ANN.AX",    # Ansell

    # ── Consumer / Retail ───────────────────────────────────────────────
    "WES.AX",    # Wesfarmers
    "WOW.AX",    # Woolworths
    "COL.AX",    # Coles
    "JBH.AX",    # JB Hi-Fi
    "HVN.AX",    # Harvey Norman
    "DMP.AX",    # Domino's Pizza
    "A2M.AX",    # a2 Milk
    "TWE.AX",    # Treasury Wine Estates
    "BRG.AX",    # Breville
    "SUL.AX",    # Super Retail Group
    "PMV.AX",    # Premier Investments
    "BAP.AX",    # Bapcor
    "EVT.AX",    # EVT (entertainment/hotels)
    "FLT.AX",    # Flight Centre
    "WEB.AX",    # Webjet
    "ALL.AX",    # Aristocrat Leisure
    "TAH.AX",    # Tabcorp
    "SGR.AX",    # Star Entertainment

    # ── Industrials ─────────────────────────────────────────────────────
    "BXB.AX",    # Brambles
    "AMC.AX",    # Amcor
    "QAN.AX",    # Qantas
    "TCL.AX",    # Transurban
    "AZJ.AX",    # Aurizon
    "DOW.AX",    # Downer EDI
    "WOR.AX",    # Worley
    "SEK.AX",    # Seek
    "ORA.AX",    # Orora
    "NUF.AX",    # Nufarm
    "RWC.AX",    # Reliance Worldwide
    "JHX.AX",    # James Hardie
    "ALQ.AX",    # ALS
    "SKC.AX",    # SkyCity Entertainment

    # ── Real Estate (REITs) ─────────────────────────────────────────────
    "GMG.AX",    # Goodman Group
    "SCG.AX",    # Scentre Group
    "DXS.AX",    # Dexus
    "GPT.AX",    # GPT Group
    "MGR.AX",    # Mirvac
    "VCX.AX",    # Vicinity Centres
    "LLC.AX",    # Lendlease
    "SGP.AX",    # Stockland
    "CHC.AX",    # Charter Hall
    "GOZ.AX",    # Growthpoint Properties
    "BWP.AX",    # BWP Trust
    "CMW.AX",    # Cromwell Property
    "NSR.AX",    # National Storage REIT
    "CQR.AX",    # Charter Hall Retail REIT
    "CLW.AX",    # Charter Hall Long WALE REIT

    # ── Technology ──────────────────────────────────────────────────────
    "XRO.AX",    # Xero
    "WTC.AX",    # WiseTech Global
    "CPU.AX",    # Computershare
    "REA.AX",    # REA Group
    "CAR.AX",    # CAR Group
    "NXT.AX",    # NextDC
    "MP1.AX",    # Megaport
    "TNE.AX",    # Technology One
    "IEL.AX",    # IDP Education

    # ── Telecom / Media ─────────────────────────────────────────────────
    "TLS.AX",    # Telstra
    "TPG.AX",    # TPG Telecom
    "REH.AX",    # Reece
    "NEC.AX",    # Nine Entertainment
    "SPK.AX",    # Spark New Zealand

    # ── Utilities ───────────────────────────────────────────────────────
    "ALD.AX",    # Ampol
    "MCY.AX",    # Mercury NZ
    "CNU.AX",    # Chorus
    "QUB.AX",    # Qube Holdings

    # ── Other ───────────────────────────────────────────────────────────
    "SOL.AX",    # WH Soul Pattinson
    "AIA.AX",    # Auckland International Airport
    "IRE.AX",    # IRESS
    "CTD.AX",    # Corporate Travel Management
    "NWL.AX",    # Netwealth
    "APE.AX",    # Eagers Automotive
    "ALX.AX",    # Atlas Arteria
    "PBH.AX",    # PointsBet
    "ELD.AX",    # Elders
    "SDF.AX",    # Steadfast Group
    "BGA.AX",    # Bega Cheese
    "MTS.AX",    # Metcash
    "ARB.AX",    # ARB Corporation
    "MEZ.AX",    # Meridian Energy
    "TYR.AX",    # Tyro Payments
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
            print(f"  [{i:3d}/{len(candidates)}] {ticker:12s} NO DATA")
        else:
            first, last, rows = result
            first_dt = datetime.strptime(first, "%Y-%m-%d")
            years = (datetime.now() - first_dt).days / 365.25

            if years < MIN_HISTORY_YEARS:
                too_short.append((ticker, first, f"{years:.1f}yr"))
                print(f"  [{i:3d}/{len(candidates)}] {ticker:12s} TOO SHORT  from {first} ({years:.1f}yr)")
            else:
                validated.append(ticker)
                print(f"  [{i:3d}/{len(candidates)}] {ticker:12s} OK         from {first} ({years:.1f}yr, {rows} rows)")

    return validated, no_data, too_short


# ═════════════════════════════════════════════════════════════════════════
#  Phase 2: METADATA — fetch name/sector/industry
# ═════════════════════════════════════════════════════════════════════════

def fetch_metadata(ticker):
    """Fetch company name, sector, industry from yfinance .info (no session)."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or ticker.replace(".AX", "")
        sector = info.get("sector", "N/A")
        industry = info.get("industry", "N/A")
        return name, sector, industry
    except Exception:
        return ticker.replace(".AX", ""), "N/A", "N/A"


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
    print("═══ ASX 200 Historical Data Download ═══")
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
