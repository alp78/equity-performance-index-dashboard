"""
Probe all candidate tickers for an index and list which are available
on Yahoo Finance with sufficient history.

Usage:
  python scripts/list_index_stocks.py BVSP
  python scripts/list_index_stocks.py AXJO
"""

import sys
import yfinance as yf
from datetime import datetime

MIN_HISTORY_YEARS = 5
START_DATE = "2016-02-22"

# Candidate tickers per index (B3 codes without .SA, ASX codes without .AX)
CANDIDATES = {
    "BVSP": {
        "suffix": ".SA",
        "tickers": [
            # Financials
            "ITUB4", "BBDC3", "BBDC4", "BBAS3", "BPAC11", "SANB11", "ITSA4",
            "B3SA3", "BBSE3", "CXSE3", "PSSA3", "IRBR3", "BRAP4",
            # Energy / Oil & Gas
            "PETR3", "PETR4", "PRIO3", "RECV3", "BRAV3", "CSAN3", "RAIZ4",
            "VBBR3", "UGPA3",
            # Mining & Metals
            "VALE3", "CSNA3", "CMIN3", "GGBR4", "GOAU4", "USIM5", "BRKM5",
            # Utilities
            "ELET3", "ELET6", "EQTL3", "CPFE3", "CMIG4", "ENGI11", "EGIE3",
            "ENEV3", "CPLE6", "TAEE11", "AURE3", "SBSP3", "ISAE4",
            # Consumer / Retail
            "ABEV3", "LREN3", "MGLU3", "AZZA3", "NTCO3", "VIVA3", "CEAB3",
            "RADL3", "HYPE3", "ASAI3", "PCAR3", "CRFB3",
            # Healthcare
            "RDOR3", "HAPV3", "FLRY3", "SMFT3",
            # Industrials
            "EMBR3", "WEGE3", "RAIL3", "CCRO3", "RENT3", "VAMO3", "POMO4",
            # Real Estate
            "CYRE3", "MRVE3", "MULT3", "ALOS3", "IGTI11", "CURY3", "DIRR3",
            # Telecom
            "VIVT3", "TIMS3",
            # Tech & Education
            "TOTS3", "YDUQ3", "COGN3",
            # Food & Agriculture
            "JBSS3", "BRFS3", "MRFG3", "BEEF3", "SLCE3", "SMTO3",
            # Paper
            "SUZB3", "KLBN11",
            # Travel
            "CVCB3", "AZUL4",
            # Other
            "DXCO3", "ALPA4", "LWSA3", "EZTC3",
        ],
    },
    "AXJO": {
        "suffix": ".AX",
        "tickers": [
            "BHP", "CBA", "CSL", "WBC", "ANZ", "NAB", "WES", "MQG", "RIO",
            "FMG", "WOW", "TLS", "ALL", "STO", "AMC", "COL", "QBE", "SUN",
            "IAG", "NCM", "ORI", "ORG", "REA", "RHC", "SGP", "TCL", "WPL",
            "XRO", "JHX", "MIN", "MPL", "NST", "OZL", "PME", "QAN", "REH",
            "RMD", "SEK", "SHL", "SOL", "TWE", "WTC", "CPU", "EVN",
            "GMG", "GOZ", "GPT", "HVN", "IEL", "IFL", "IGO", "LLC",
            "MGR", "NHF", "NXT", "ORA", "OSH", "PLS", "PPT", "S32",
            "SCG", "SGM", "SKC", "SPK", "STO", "SVW", "TAH", "VCX",
            "WHC", "WOR", "AGL", "ALX", "APE", "ASX", "AZJ", "BOQ",
            "BEN", "BXB", "CAR", "CCL", "CHC", "CIM", "CMW", "COH",
            "CWY", "DXS", "EBO", "EDV", "ELD", "EVT", "FLT", "GNC",
            "HUB", "IDX", "ILU", "IPL", "JBH", "KAR", "LNK", "LYC",
            "MFG", "MMS", "MP1", "NEC", "NWL", "ORI", "PDN", "PNI",
            "PRU", "PTM", "RRL", "SGR", "SIG", "SIQ", "SKI", "SUL",
            "SWM", "TPG", "URW", "VEA", "VOC", "WAM", "WEB", "Z1P",
        ],
    },
}


def probe_ticker(yahoo_ticker):
    """Check if ticker has data and how far back it goes.
    Uses yf.Ticker().history() which works reliably in yfinance 1.1.0.
    Returns (first_date_str, last_date_str, num_rows) or None."""
    try:
        t = yf.Ticker(yahoo_ticker)
        # Quick check: recent data exists?
        df = t.history(period="5d")
        if df is None or df.empty:
            return None
        # Check full range
        df_full = t.history(start="2010-01-01", end=datetime.now().strftime("%Y-%m-%d"))
        if df_full is None or df_full.empty:
            return None
        first = df_full.index[0].strftime("%Y-%m-%d")
        last = df_full.index[-1].strftime("%Y-%m-%d")
        return first, last, len(df_full)
    except Exception:
        return None


def main():
    idx = (sys.argv[1] if len(sys.argv) > 1 else "BVSP").strip().upper().lstrip("^")

    if idx not in CANDIDATES:
        print(f"Unknown index: {idx}. Available: {', '.join(CANDIDATES.keys())}")
        sys.exit(1)

    cfg = CANDIDATES[idx]
    suffix = cfg["suffix"]
    tickers = cfg["tickers"]
    print(f"═══ Probing {len(tickers)} candidates for ^{idx} ═══")
    print(f"  Suffix: {suffix}  |  Min history: {MIN_HISTORY_YEARS}yr  |  Start: {START_DATE}\n")

    available = []
    no_data = []
    too_short = []

    for i, code in enumerate(tickers, 1):
        yahoo = f"{code}{suffix}"
        result = probe_ticker(yahoo)

        if result is None:
            no_data.append(code)
            print(f"  [{i:3d}/{len(tickers)}] {yahoo:15s} NO DATA")
        else:
            first, last, rows = result
            first_dt = datetime.strptime(first, "%Y-%m-%d")
            years = (datetime.now() - first_dt).days / 365.25

            if years < MIN_HISTORY_YEARS:
                too_short.append((code, first, f"{years:.1f}yr"))
                print(f"  [{i:3d}/{len(tickers)}] {yahoo:15s} TOO SHORT  from {first} ({years:.1f}yr, {rows} rows)")
            else:
                available.append((yahoo, first, last, rows))
                print(f"  [{i:3d}/{len(tickers)}] {yahoo:15s} OK         from {first} ({years:.1f}yr, {rows} rows)")

    # ── Summary ─────────────────────────────────────────────────────────
    print(f"\n═══ Results ═══")
    print(f"  Available ({len(available)}):")
    for yahoo, first, last, rows in available:
        print(f"    {yahoo}")

    if too_short:
        print(f"\n  Too short — excluded ({len(too_short)}):")
        for code, first, yrs in too_short:
            print(f"    {code}{suffix}  (from {first}, {yrs})")

    if no_data:
        print(f"\n  No data on Yahoo ({len(no_data)}):")
        print(f"    {', '.join(no_data)}")

    # Output a Python list for easy copy-paste into download script
    print(f"\n═══ Copy-paste list ({len(available)} tickers) ═══")
    print("TICKERS = [")
    for yahoo, _, _, _ in available:
        print(f'    "{yahoo}",')
    print("]")


if __name__ == "__main__":
    main()
