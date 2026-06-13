import os
import math
import tempfile
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from jugaad_data.nse import bhavcopy_save

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_CSV = os.path.join(ROOT, "fundamentals_master.csv")


def latest_market_day():
    d = date.today()
    for _ in range(10):
        if d.weekday() < 5:
            return d
        d -= timedelta(days=1)
    return date.today()


def load_nse_universe():
    d = latest_market_day()
    with tempfile.TemporaryDirectory() as tmp:
        bhavcopy_save(d, tmp)
        files = [f for f in os.listdir(tmp) if f.lower().endswith(".csv")]
        if not files:
            raise FileNotFoundError("Bhavcopy CSV not downloaded")
        path = os.path.join(tmp, files[0])
        df = pd.read_csv(path)

    if "SYMBOL" in df.columns and "SERIES" in df.columns:
        eq = df[df["SERIES"] == "EQ"].copy()
        eq["Ticker"] = eq["SYMBOL"].astype(str).str.upper()
    elif "TckrSymb" in df.columns and "SctySrs" in df.columns and "FinInstrmTp" in df.columns:
        eq = df[(df["FinInstrmTp"] == "STK") & (df["SctySrs"] == "EQ")].copy()
        eq["Ticker"] = eq["TckrSymb"].astype(str).str.upper()
    else:
        raise ValueError("Bhavcopy format not recognized")

    eq = eq[["Ticker"]].drop_duplicates().reset_index(drop=True)
    return eq


def pct100(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x) * 100, 2)


def mcap_cr(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x) / 1e7, 2)


def compute_roce(info):
    ebit = info.get("ebit")
    total_assets = info.get("totalAssets")
    current_liab = info.get("totalCurrentLiabilities")
    if ebit is None or total_assets is None or current_liab is None:
        return None
    cap_employed = total_assets - current_liab
    if cap_employed and cap_employed > 0:
        return round((ebit / cap_employed) * 100, 2)
    return None


def fetch_one(ticker):
    yf_ticker = yf.Ticker(f"{ticker}.NS")
    info = yf_ticker.info

    return {
        "Ticker": ticker,
        "MCap_Cr": mcap_cr(info.get("marketCap")),
        "ROE_Latest": pct100(info.get("returnOnEquity")),
        "ROCE_Latest": compute_roce(info),
        "OPM_Latest": pct100(info.get("operatingMargins")),
        "Revenue_CAGR_AllYears": pct100(info.get("revenueGrowth")),
        "PAT_CAGR_AllYears": pct100(info.get("earningsGrowth")),
    }


def main():
    universe = load_nse_universe()
    rows = []

    for ticker in universe["Ticker"].tolist():
        try:
            rows.append(fetch_one(ticker))
        except Exception:
            rows.append({
                "Ticker": ticker,
                "MCap_Cr": None,
                "ROE_Latest": None,
                "ROCE_Latest": None,
                "OPM_Latest": None,
                "Revenue_CAGR_AllYears": None,
                "PAT_CAGR_AllYears": None,
            })

    df = pd.DataFrame(rows)
    df = df.sort_values(["MCap_Cr", "Ticker"], ascending=[False, True])
    df.to_csv(OUT_CSV, index=False)


if __name__ == "__main__":
    main()
