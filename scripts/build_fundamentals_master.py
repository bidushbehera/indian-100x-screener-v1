import os
import math
import time
import random
import tempfile
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from jugaad_data.nse import bhavcopy_save

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_CSV = os.path.join(ROOT, "fundamentals_master.csv")
LOG_CSV = os.path.join(ROOT, "fundamentals_master_failures.csv")

MAX_RETRIES = 3
SLEEP_MIN = 0.4
SLEEP_MAX = 1.2


def latest_market_day():
    d = date.today()
    for _ in range(10):
        if d.weekday() < 5:
            return d
        d -= timedelta(days=1)
    return date.today()


def _find_col(df, candidates):
    cols = {c.upper(): c for c in df.columns}
    for cand in candidates:
        if cand.upper() in cols:
            return cols[cand.upper()]
    return None


def load_nse_universe():
    d = latest_market_day()
    with tempfile.TemporaryDirectory() as tmp:
        bhavcopy_save(d, tmp)
        files = [f for f in os.listdir(tmp) if f.lower().endswith(".csv")]
        if not files:
            raise FileNotFoundError("Bhavcopy CSV not downloaded")
        path = os.path.join(tmp, files[0])
        df = pd.read_csv(path)

    symbol_col = _find_col(df, ["SYMBOL", "TckrSymb"])
    series_col = _find_col(df, ["SERIES", "SctySrs"])
    instr_col = _find_col(df, ["FinInstrmTp"])
    name_col = _find_col(df, ["NAME OF COMPANY", "FinInstrmNm"])

    if symbol_col is None:
        raise ValueError("Ticker symbol column not found in bhavcopy")

    eq = df.copy()

    if instr_col and series_col:
        eq = eq[(eq[instr_col].astype(str).str.upper() == "STK") &
                (eq[series_col].astype(str).str.upper() == "EQ")]
    elif series_col:
        eq = eq[eq[series_col].astype(str).str.upper() == "EQ"]

    eq["Ticker"] = eq[symbol_col].astype(str).str.upper().str.strip()

    if name_col:
        eq["CompanyName"] = eq[name_col].astype(str).str.upper().str.strip()
    else:
        eq["CompanyName"] = ""

    bad_keywords = [
        "ETF", "BEES", "INDEX", "GOLD", "SILVER", "LIQUID", "GILT",
        "SENSEX", "NIFTY", "BANKEX", "MOMENTUM", "QUALITY", "VALUE",
        "LOWVOL", "ALPHA", "NEXT50", "MIDCAP", "SMALLCAP", "PSUBANK",
        "INFRA", "REALTY"
    ]

    def looks_non_equity(row):
        ticker = str(row["Ticker"])
        cname = str(row["CompanyName"])
        text = f"{ticker} {cname}"
        return any(k in text for k in bad_keywords)

    eq = eq[~eq.apply(looks_non_equity, axis=1)].copy()
    eq = eq[["Ticker"]].drop_duplicates().reset_index(drop=True)
    return eq


def is_missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def pct100(x):
    if is_missing(x):
        return None
    try:
        val = float(x) * 100.0
    except Exception:
        return None
    if not math.isfinite(val):
        return None
    if val < -10000 or val > 100000:
        return None
    return round(val, 2)


def mcap_cr(x):
    if is_missing(x):
        return None
    try:
        val = float(x) / 1e7
    except Exception:
        return None
    if not math.isfinite(val) or val <= 0:
        return None
    return round(val, 2)


def safe_num(info, key):
    try:
        v = info.get(key)
    except Exception:
        return None
    if is_missing(v):
        return None
    try:
        v = float(v)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    return v


def compute_roce(info):
    ebit = safe_num(info, "ebit")
    total_assets = safe_num(info, "totalAssets")
    current_liab = safe_num(info, "totalCurrentLiabilities")

    if ebit is None or total_assets is None or current_liab is None:
        return None

    cap_employed = total_assets - current_liab
    if cap_employed <= 0:
        return None

    roce = (ebit / cap_employed) * 100.0
    if not math.isfinite(roce):
        return None
    if roce < -1000 or roce > 1000:
        return None
    return round(roce, 2)


def sanitize_margin(x):
    val = pct100(x)
    if val is None:
        return None
    if val < -200 or val > 200:
        return None
    return val


def sanitize_growth(x):
    val = pct100(x)
    if val is None:
        return None
    if val < -1000 or val > 10000:
        return None
    return val


def fetch_info_with_retry(ticker):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            obj = yf.Ticker(f"{ticker}.NS")
            info = obj.info
            if isinstance(info, dict) and len(info) > 5:
                return info, None
            last_err = f"Empty/short info dict on attempt {attempt}"
        except Exception as e:
            last_err = str(e)

        time.sleep((1.2 * attempt) + random.uniform(SLEEP_MIN, SLEEP_MAX))

    return None, last_err


def fetch_one(ticker):
    info, err = fetch_info_with_retry(ticker)
    if info is None:
        return {
            "Ticker": ticker,
            "MCap_Cr": None,
            "ROE_Latest": None,
            "ROCE_Latest": None,
            "OPM_Latest": None,
            "Revenue_CAGR_AllYears": None,
            "PAT_CAGR_AllYears": None,
        }, err

    row = {
        "Ticker": ticker,
        "MCap_Cr": mcap_cr(safe_num(info, "marketCap")),
        "ROE_Latest": pct100(safe_num(info, "returnOnEquity")),
        "ROCE_Latest": compute_roce(info),
        "OPM_Latest": sanitize_margin(safe_num(info, "operatingMargins")),
        "Revenue_CAGR_AllYears": sanitize_growth(safe_num(info, "revenueGrowth")),
        "PAT_CAGR_AllYears": sanitize_growth(safe_num(info, "earningsGrowth")),
    }
    return row, None


def main():
    universe = load_nse_universe()
    rows = []
    failures = []

    tickers = universe["Ticker"].tolist()
    total = len(tickers)

    for i, ticker in enumerate(tickers, start=1):
        row, err = fetch_one(ticker)
        rows.append(row)

        if err:
            failures.append({"Ticker": ticker, "Error": err})

        if i % 25 == 0:
            print(f"Processed {i}/{total}")

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["Ticker"]).copy()
    df = df.sort_values(["MCap_Cr", "Ticker"], ascending=[False, True], na_position="last")

    for col in [
        "MCap_Cr", "ROE_Latest", "ROCE_Latest", "OPM_Latest",
        "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears"
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df.to_csv(OUT_CSV, index=False)

    fail_df = pd.DataFrame(failures)
    fail_df.to_csv(LOG_CSV, index=False)


if __name__ == "__main__":
    main()
