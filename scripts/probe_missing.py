"""
Probe alternative Yahoo Finance ticker formats for Brazilian stocks
that failed with their standard B3 code.
"""

import yfinance as yf

# Tickers that returned NO DATA with standard {CODE}.SA format
# Try: different share classes, old names, ADRs
ALTERNATIVES = {
    "ELET3":  ["ELET3.SA", "ELET5.SA", "ELET6.SA", "ELTO3.SA", "EBR", "ELET.SA"],
    "ELET6":  ["ELET6.SA", "ELET3.SA", "ELET5.SA", "EBR.B", "ELET.SA"],
    "CPLE6":  ["CPLE6.SA", "CPLE3.SA", "CPLE5.SA", "CPLE11.SA", "ELP"],
    "NTCO3":  ["NTCO3.SA", "NATU3.SA", "NTCO.SA", "NTZ"],
    "CRFB3":  ["CRFB3.SA", "CRFB.SA", "GFSA3.SA"],
    "EMBR3":  ["EMBR3.SA", "EMBR4.SA", "EMBR.SA", "ERJ"],
    "CCRO3":  ["CCRO3.SA", "CCRO.SA"],
    "JBSS3":  ["JBSS3.SA", "JBSS.SA"],
    "BRFS3":  ["BRFS3.SA", "BRFS.SA", "BRFS"],
    "MRFG3":  ["MRFG3.SA", "MRFG.SA"],
    "AZUL4":  ["AZUL4.SA", "AZUL3.SA", "AZUL.SA", "AZUL"],
}


def probe(ticker):
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="5d")
        if df is not None and not df.empty:
            first_full = t.history(start="2010-01-01")
            first_date = first_full.index[0].strftime("%Y-%m-%d") if first_full is not None and not first_full.empty else "?"
            info = t.info or {}
            name = info.get("shortName", info.get("longName", ""))
            return True, first_date, name
    except Exception:
        pass
    return False, None, None


def main():
    print("═══ Probing alternative tickers for missing Bovespa stocks ═══\n")

    for b3_code, alternatives in ALTERNATIVES.items():
        print(f"{b3_code}:")
        found = False
        for alt in alternatives:
            ok, first_date, name = probe(alt)
            if ok:
                print(f"  ✓ {alt:15s} WORKS  from {first_date}  ({name})")
                found = True
            else:
                print(f"    {alt:15s} no data")
        if not found:
            print(f"  ✗ No working ticker found")
        print()


if __name__ == "__main__":
    main()
