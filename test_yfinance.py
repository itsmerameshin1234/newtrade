"""
test_yfinance.py
─────────────────
Inspect all fields returned by yfinance for any NSE symbol.

Usage:
    python3 test_yfinance.py            # defaults to HDFCBANK
    python3 test_yfinance.py RELIANCE
    python3 test_yfinance.py INFY
"""

import sys
import yfinance as yf

# ── symbol from CLI arg or default ─────────────────────────────────────────────
sym = sys.argv[1].upper() if len(sys.argv) > 1 else "HDFCBANK"
ticker_sym = sym + ".NS"

print(f"\n{'='*65}")
print(f"  yfinance fields for: {ticker_sym}")
print(f"{'='*65}\n")

tk = yf.Ticker(ticker_sym)
info = tk.info

if not info:
    print("No data returned. Check the symbol name.")
    sys.exit(1)

print(f"  Total fields available: {len(info)}\n")

# ── group and print by category ────────────────────────────────────────────────
categories = {
    "Identity / Company": [
        "symbol", "longName", "shortName", "quoteType",
        "sector", "sectorKey", "industry", "industryKey",
        "fullExchangeName", "exchange", "currency", "country",
        "city", "website", "fullTimeEmployees", "longBusinessSummary",
    ],
    "Price (live)": [
        "currentPrice", "previousClose", "open",
        "dayHigh", "dayLow", "regularMarketPrice",
        "regularMarketOpen", "regularMarketDayHigh", "regularMarketDayLow",
        "regularMarketVolume", "bid", "ask",
    ],
    "52-Week / Moving Averages": [
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "fiftyTwoWeekRange", "fiftyTwoWeekChangePercent",
        "fiftyDayAverage", "twoHundredDayAverage",
        "allTimeHigh", "allTimeLow",
    ],
    "Volume": [
        "volume", "averageVolume",
        "averageDailyVolume10Day", "averageDailyVolume3Month",
    ],
    "Market Cap / Shares": [
        "marketCap", "enterpriseValue",
        "sharesOutstanding", "floatShares", "impliedSharesOutstanding",
    ],
    "Valuation Ratios": [
        "trailingPE", "forwardPE", "priceToBook",
        "pegRatio", "trailingPegRatio",
        "priceToSalesTrailing12Months", "enterpriseToRevenue",
    ],
    "Earnings / EPS": [
        "trailingEps", "forwardEps", "epsCurrentYear",
        "earningsGrowth", "earningsQuarterlyGrowth",
        "revenueGrowth", "profitMargins",
        "operatingMargins", "ebitdaMargins",
        "returnOnEquity", "returnOnAssets",
        "grossProfits", "totalRevenue", "netIncomeToCommon",
    ],
    "Dividends": [
        "dividendRate", "dividendYield",
        "trailingAnnualDividendRate", "trailingAnnualDividendYield",
        "fiveYearAvgDividendYield", "payoutRatio",
        "exDividendDate", "lastDividendDate", "lastDividendValue",
    ],
    "Risk / Beta": [
        "beta", "52WeekChange", "SandP52WeekChange",
    ],
    "Book / Cash / Debt": [
        "bookValue", "totalCash", "totalCashPerShare",
        "totalDebt", "revenuePerShare",
    ],
    "Analyst Ratings": [
        "recommendationKey", "recommendationMean",
        "averageAnalystRating", "numberOfAnalystOpinions",
        "targetHighPrice", "targetLowPrice",
        "targetMeanPrice", "targetMedianPrice",
    ],
    "Institutional Ownership": [
        "heldPercentInsiders", "heldPercentInstitutions",
    ],
    "Earnings Dates": [
        "earningsTimestamp", "earningsTimestampStart",
        "earningsTimestampEnd", "mostRecentQuarter",
        "lastFiscalYearEnd", "nextFiscalYearEnd",
    ],
    "Other": [],   # catch-all for anything not in above lists
}

# Collect all keys already shown
shown = set()
for cat, keys in categories.items():
    shown.update(keys)

# Put leftover keys into "Other"
categories["Other"] = [k for k in sorted(info.keys()) if k not in shown]

# ── print each category ────────────────────────────────────────────────────────
for cat, keys in categories.items():
    available = [(k, info[k]) for k in keys if k in info]
    if not available:
        continue

    print(f"  ── {cat} {'─' * (50 - len(cat))}")
    for k, v in available:
        # truncate long strings
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        elif isinstance(v, list):
            v = f"[list of {len(v)} items]"
        print(f"    {k:<45} {v}")
    print()

# ── fast_info ─────────────────────────────────────────────────────────────────
print(f"  ── fast_info {'─' * 50}")
try:
    fi = tk.fast_info
    for attr in dir(fi):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(fi, attr)
            if callable(val):
                continue
            print(f"    {attr:<45} {val}")
        except Exception:
            pass
except Exception as e:
    print(f"  fast_info error: {e}")

print(f"\n{'='*65}\n")
