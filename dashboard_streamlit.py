import time
from typing import Dict, Any, List

import pandas as pd
import streamlit as st
import yfinance as yf

nse_prices_df = None
equity_universe_df = None
fundamentals_lookup: Dict[str, Any] = {}

st.sidebar.caption(f"yfinance version: {yf.__version__}")

st.set_page_config(page_title="100X Screener V1 - Indian Equities", layout="wide")

st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "V1 = Single-page Streamlit app using free Yahoo Finance data via yfinance. "
    "Acts as a narrowing engine, not a buy/sell signal."
)

with st.expander("What this V1 actually does / does NOT do", expanded=False):
    st.markdown(
        """
- **Implements (current scope):**
  - Screens NSE stocks using `yfinance.Ticker.info` plus your curated `fundamentals_master.csv`.
  - Applies a **Fix 1 universe gate** built for small-cap / lower mid-cap deep-value hunting.
  - Computes L1–L5, Conviction, WeightedScore, and a `ScreenVerdict`.
  - Displays results in a table with CSV download.

- **Does *not* implement yet (later stages):**
  - RPT analysis.
  - Inter-corporate deposit checks.
  - Auditor remark extraction.
  - Dilution history review.
  - Promoter remuneration tests.
  - Soft sector/moat/tailwind analysis.
"""
    )

st.markdown(
    "<sub>yfinance is an unofficial wrapper around Yahoo Finance; coverage and reliability "
    "especially for Indian fundamentals and shareholding data are limited.</sub>",
    unsafe_allow_html=True,
)

DEFAULT_UNIVERSE: List[str] = [
    "LLOYDSME.NS",
    "POLYCAB.NS",
    "DEEPAKNTR.NS",
    "CGPOWER.NS",
    "TANLA.NS",
    "KPITTECH.NS",
    "CDSL.NS",
    "CAMS.NS",
    "IRCTC.NS",
    "OLECTRA.NS",
]

CONFIG: Dict[str, Any] = {
    "turnover_min_cr": 1.0,
    "turnover_max_cr": 50.0,
    "mcap_min_cr": 200.0,
    "mcap_max_cr": 5000.0,
    "pe_max": 20.0,
    "peg_max": 1.2,
    "ev_ebitda_max": 12.0,
    "pb_max": 3.0,
    "roce_min": 0.20,
    "roe_min": 0.15,
    "roa_min": 0.08,
    "opm_min": 0.12,
    "rev_growth_min": 0.12,
    "earn_growth_min": 0.15,
    "ocf_pat_min": 0.8,
    "fcf_yield_min": 0.03,
    "de_max": 0.5,
    "interest_coverage_min": 4.0,
    "insider_min": 0.35,
    "insider_strong": 0.50,
    "insider_excellent": 0.60,
    "quality_min_raw": 5,
}

VERDICT_PASS = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA = "FAIL (Insufficient data)"


def safe(info: Dict[str, Any], key: str, default=None):
    v = info.get(key, default)
    if v in (None, "N/A", "NaN"):
        return default
    return v


def parse_percent_or_float(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            num = float(text)
        except Exception:
            return None
    else:
        try:
            num = float(value)
        except Exception:
            return None
    return num / 100.0 if num > 1.5 else num


def approx_quality_score(info: Dict[str, Any]) -> int:
    score = 0
    ni = safe(info, "netIncomeToCommon") or 0
    ocf = safe(info, "operatingCashflow") or 0
    roa = safe(info, "returnOnAssets") or 0
    ltd = safe(info, "longTermDebt") or 0
    ta = safe(info, "totalAssets") or 0
    cr = safe(info, "currentRatio") or 0
    gm = safe(info, "grossMargins") or 0
    rg = safe(info, "revenueGrowth") or 0

    if ni > 0:
        score += 1
    if ocf > 0:
        score += 1
    if roa and roa > 0.05:
        score += 1
    if ocf > ni > 0:
        score += 1
    if ta > 0 and (ltd / ta) < 0.3:
        score += 1
    if cr and cr > 1.5:
        score += 1
    if gm and gm > 0.2 and rg and rg > 0:
        score += 1

    return score


def load_fundamentals_master() -> pd.DataFrame:
    try:
        return pd.read_csv("fundamentals_master.csv")
    except Exception as e:
        st.warning(f"Could not load fundamentals_master.csv: {e}")
        return pd.DataFrame()


def load_stock_master() -> pd.DataFrame:
    try:
        return pd.read_csv("stock_master.csv")
    except Exception as e:
        st.warning(f"Could not load stock_master.csv: {e}")
        return pd.DataFrame()


def rebuild_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> None:
    global fundamentals_lookup
    fundamentals_lookup = {}
    if fundamentals_master_df is None or fundamentals_master_df.empty:
        return
    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    fundamentals_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()

    df = nse_df.copy()
    required_cols = ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"NSE CSV is missing required column: {col}")
            return pd.DataFrame()

    df = df[df["FinInstrmTp"] == "STK"]
    df = df[df["SctySrs"] == "EQ"]

    if df.empty:
        return pd.DataFrame()

    df = df[["TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol", "TtlTrfVal"]].copy()
    df = df.rename(
        columns={
            "TckrSymb": "Ticker",
            "SctySrs": "Series",
            "ClsPric": "Close",
            "TtlTradgVol": "Volume",
            "TtlTrfVal": "Turnover",
        }
    )

    df["Turnover_Cr"] = pd.to_numeric(df["Turnover"], errors="coerce") / 1e7
    df = df[
        (df["Turnover_Cr"] >= CONFIG["turnover_min_cr"])
        & (df["Turnover_Cr"] <= CONFIG["turnover_max_cr"])
    ]

    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def compute_screen_verdict(
    l1_val: bool,
    l2_prof: bool,
    l3_cf: bool,
    l4_share: bool,
    l5_forensic: bool,
    l1_data_missing: bool,
    l2_data_missing: bool,
    l3_data_missing: bool,
    l4_data_missing: bool,
    l5_data_missing: bool,
) -> str:
    layers_missing = [
        l1_data_missing,
        l2_data_missing,
        l3_data_missing,
        l4_data_missing,
        l5_data_missing,
    ]
    layers_pass = [l1_val, l2_prof, l3_cf, l4_share, l5_forensic]
    testable_count = sum(1 for m in layers_missing if not m)

    if testable_count < 3:
        return VERDICT_FAIL_NODATA

    genuine_failure = any(
        (not passed) and (not missing)
        for passed, missing in zip(layers_pass, layers_missing)
    )
    if genuine_failure:
        return VERDICT_FAIL_GENUINE

    if any(layers_missing):
        return VERDICT_PASS_DATAGAP

    return VERDICT_PASS


def evaluate_stock(ticker: str) -> Dict[str, Any]:
    try:
        yf_ticker = yf.Ticker(ticker)
        base_ticker = ticker.replace(".NS", "").upper()
        fund_row = fundamentals_lookup.get(base_ticker)
        info = yf_ticker.info

        pe = safe(info, "trailingPE")
        pb = safe(info, "priceToBook")
        ev_ebitda = safe(info, "enterpriseToEbitda")

        roe = safe(info, "returnOnEquity")
        roa = safe(info, "returnOnAssets")
        opm = safe(info, "operatingMargins")
        revg = safe(info, "revenueGrowth")
        earng = safe(info, "earningsGrowth")

        fcf = safe(info, "freeCashflow")
        ocf = safe(info, "operatingCashflow")
        ni = safe(info, "netIncomeToCommon")

        de = safe(info, "debtToEquity")
        insider = safe(info, "heldPercentInsiders")
        interest_coverage = safe(info, "interestCoverage")

        mcap_raw = safe(info, "marketCap") or 0
        price = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector = safe(info, "sector", "N/A")

        ebit = safe(info, "ebit")
        ta = safe(info, "totalAssets")
        current_liab = safe(info, "totalCurrentLiabilities")

        mcap_cr = mcap_raw / 1e7 if mcap_raw else None

        roce = None
        if ebit and ta and current_liab is not None:
            capital_employed = ta - current_liab
            if capital_employed > 0:
                roce = ebit / capital_employed

        peg = None
        if pe and earng and earng > 0:
            peg = pe / (earng * 100.0)

        ocf_pat = None
        if ocf and ni and ni > 0:
            ocf_pat = ocf / ni

        fcf_yield = None
        if fcf and mcap_raw:
            fcf_yield = fcf / mcap_raw

        de_ratio = None
        if de is not None and de >= 0:
            de_ratio = float(de)
            if de_ratio > 10:
                de_ratio = None

        quality_raw = approx_quality_score(info)

        if fund_row is not None:
            if "ROE_Latest" in fund_row.index:
                v = parse_percent_or_float(fund_row["ROE_Latest"])
                if v is not None:
                    roe = v
            if "ROCE_Latest" in fund_row.index:
                v = parse_percent_or_float(fund_row["ROCE_Latest"])
                if v is not None:
                    roce = v
            if "OPM_Latest" in fund_row.index:
                v = parse_percent_or_float(fund_row["OPM_Latest"])
                if v is not None:
                    opm = v
            if "Revenue_CAGR_AllYears" in fund_row.index:
                v = parse_percent_or_float(fund_row["Revenue_CAGR_AllYears"])
                if v is not None:
                    revg = v
            if "PAT_CAGR_AllYears" in fund_row.index:
                v = parse_percent_or_float(fund_row["PAT_CAGR_AllYears"])
                if v is not None:
                    earng = v

        universe_mcap_pass = (
            mcap_cr is not None
            and CONFIG["mcap_min_cr"] <= mcap_cr <= CONFIG["mcap_max_cr"]
        )

        l1_checks = [
            pe is not None and pe < CONFIG["pe_max"],
            peg is not None and peg < CONFIG["peg_max"],
            ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
            pb is not None and pb < CONFIG["pb_max"],
            universe_mcap_pass,
        ]
        l1_available = [
            pe is not None,
            peg is not None,
            ev_ebitda is not None,
            pb is not None,
            mcap_cr is not None,
        ]
        l1_val = sum(l1_checks) >= 3
        l1_data_missing = sum(l1_available) < 3

        l2_checks = [
            roce is not None and roce > CONFIG["roce_min"],
            roe is not None and roe > CONFIG["roe_min"],
            roa is not None and roa > CONFIG["roa_min"],
            opm is not None and opm > CONFIG["opm_min"],
            revg is not None and revg > CONFIG["rev_growth_min"],
            earng is not None and earng > CONFIG["earn_growth_min"],
        ]
        l2_available = [
            roce is not None,
            roe is not None,
            roa is not None,
            opm is not None,
            revg is not None,
            earng is not None,
        ]
        l2_prof = sum(l2_checks) >= 4
        l2_data_missing = sum(l2_available) < 4

        l5_fields_present = sum(
            [
                safe(info, "netIncomeToCommon") is not None,
                safe(info, "operatingCashflow") is not None,
                safe(info, "returnOnAssets") is not None,
                safe(info, "longTermDebt") is not None,
                safe(info, "totalAssets") is not None,
                safe(info, "currentRatio") is not None,
                safe(info, "grossMargins") is not None,
            ]
        )
        l5_forensic = quality_raw >= CONFIG["quality_min_raw"]
        l5_data_missing = l5_fields_present < 4

        l3_checks = [
            ocf_pat is not None and ocf_pat > CONFIG["ocf_pat_min"],
            fcf_yield is not None and fcf_yield > CONFIG["fcf_yield_min"],
            de_ratio is not None and de_ratio < CONFIG["de_max"],
            interest_coverage is not None and interest_coverage > CONFIG["interest_coverage_min"],
        ]
        l3_available = [
            ocf_pat is not None,
            fcf_yield is not None,
            de_ratio is not None,
            interest_coverage is not None,
        ]
        if l2_prof and l5_forensic:
            l3_cf = sum(l3_checks) >= 1
        else:
            l3_cf = sum(l3_checks) >= 2
        l3_data_missing = sum(l3_available) < 2

        l4_share = insider is not None and insider > CONFIG["insider_min"]
        l4_data_missing = insider is None

        conviction = sum([l1_val, l2_prof, l3_cf, l4_share, l5_forensic])

        screen_verdict = compute_screen_verdict(
            l1_val,
            l2_prof,
            l3_cf,
            l4_share,
            l5_forensic,
            l1_data_missing,
            l2_data_missing,
            l3_data_missing,
            l4_data_missing,
            l5_data_missing,
        )

        # OWNERSHIP-SOFTENING TEST:
        # L4_Share is no longer required for final_pass.
        final_pass = bool(
            universe_mcap_pass
            and l2_prof
            and l5_forensic
            and conviction >= 4
        )

        weighted_score = 0
        weighted_score += 5 if pe is not None and pe < CONFIG["pe_max"] else 0
        weighted_score += 5 if peg is not None and peg < CONFIG["peg_max"] else 0
        weighted_score += 5 if ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"] else 0
        weighted_score += 3 if pb is not None and pb < CONFIG["pb_max"] else 0
        weighted_score += 2 if universe_mcap_pass else 0

        weighted_score += 8 if roce is not None and roce > CONFIG["roce_min"] else 0
        weighted_score += 6 if roe is not None and roe > CONFIG["roe_min"] else 0
        weighted_score += 4 if roa is not None and roa > CONFIG["roa_min"] else 0
        weighted_score += 4 if opm is not None and opm > CONFIG["opm_min"] else 0
        weighted_score += 4 if revg is not None and revg > CONFIG["rev_growth_min"] else 0
        weighted_score += 4 if earng is not None and earng > CONFIG["earn_growth_min"] else 0

        weighted_score += 6 if ocf_pat is not None and ocf_pat > CONFIG["ocf_pat_min"] else 0
        weighted_score += 6 if fcf_yield is not None and fcf_yield > CONFIG["fcf_yield_min"] else 0
        weighted_score += 4 if de_ratio is not None and de_ratio < CONFIG["de_max"] else 0
        weighted_score += 4 if interest_coverage is not None and interest_coverage > CONFIG["interest_coverage_min"] else 0

        if insider is not None:
            if insider >= CONFIG["insider_excellent"]:
                weighted_score += 10
            elif insider >= CONFIG["insider_strong"]:
                weighted_score += 7
            elif insider >= CONFIG["insider_min"]:
                weighted_score += 5

        quality_points = round(10 * quality_raw / 7) if quality_raw is not None else 0
        weighted_score += min(quality_points, 10)

        return {
            "Ticker": ticker.replace(".NS", ""),
            "Sector": sector,
            "ScreenVerdict": screen_verdict,
            "Universe_MCap_Pass": universe_mcap_pass,
            "Price": price,
            "MCap_Cr": round(mcap_cr, 1) if mcap_cr is not None else None,
            "PE": round(pe, 2) if pe is not None else None,
            "PB": round(pb, 2) if pb is not None else None,
            "PEG": round(peg, 2) if peg is not None else None,
            "ROCE_pct": round(roce * 100, 1) if roce is not None else None,
            "ROE_pct": round(roe * 100, 1) if roe is not None else None,
            "ROA_pct": round(roa * 100, 1) if roa is not None else None,
            "OPM_pct": round(opm * 100, 1) if opm is not None else None,
            "RevGrowth_pct": round(revg * 100, 1) if revg is not None else None,
            "EarnGrowth_pct": round(earng * 100, 1) if earng is not None else None,
            "OCF_PAT": round(ocf_pat, 2) if ocf_pat is not None else None,
            "FCFYield_pct": round(fcf_yield * 100, 2) if fcf_yield is not None else None,
            "InterestCoverage": round(float(interest_coverage), 2) if interest_coverage is not None else None,
            "Insider_pct": round(insider * 100, 1) if insider is not None else None,
            "QualityScore_raw": quality_raw,
            "L1_Val": l1_val,
            "L2_Prof": l2_prof,
            "L3_CF": l3_cf,
            "L4_Share": l4_share,
            "L5_Forensic": l5_forensic,
            "L1_DataMissing": l1_data_missing,
            "L2_DataMissing": l2_data_missing,
            "L3_DataMissing": l3_data_missing,
            "L4_DataMissing": l4_data_missing,
            "L5_DataMissing": l5_data_missing,
            "Conviction": conviction,
            "WeightedScore": weighted_score,
            "Pass": final_pass,
            "HasFundamentals": fund_row is not None,
            "Error": None,
        }

    except Exception as e:
        base_ticker = ticker.replace(".NS", "")
        return {
            "Ticker": base_ticker,
            "Sector": None,
            "ScreenVerdict": VERDICT_FAIL_NODATA,
            "Universe_MCap_Pass": False,
            "Price": None,
            "MCap_Cr": None,
            "PE": None,
            "PB": None,
            "PEG": None,
            "ROCE_pct": None,
            "ROE_pct": None,
            "ROA_pct": None,
            "OPM_pct": None,
            "RevGrowth_pct": None,
            "EarnGrowth_pct": None,
            "OCF_PAT": None,
            "FCFYield_pct": None,
            "InterestCoverage": None,
            "Insider_pct": None,
            "QualityScore_raw": None,
            "L1_Val": False,
            "L2_Prof": False,
            "L3_CF": False,
            "L4_Share": False,
            "L5_Forensic": False,
            "L1_DataMissing": True,
            "L2_DataMissing": True,
            "L3_DataMissing": True,
            "L4_DataMissing": True,
            "L5_DataMissing": True,
            "Conviction": 0,
            "WeightedScore": 0,
            "Pass": False,
            "HasFundamentals": False,
            "Error": str(e),
        }


st.sidebar.header("Controls")
min_score = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)
show_datagap = st.sidebar.checkbox(
    "Also show PASS (Data gaps present)",
    value=True,
    help="Include stocks that pass all testable layers but have some missing data fields.",
)
max_stocks = st.sidebar.number_input(
    "Max stocks to screen after turnover-band filter",
    min_value=10,
    max_value=1000,
    value=500,
    step=10,
)

st.sidebar.markdown("---")
st.sidebar.subheader("Fix 1 universe gate")
st.sidebar.write(f"Turnover band: ₹{CONFIG['turnover_min_cr']:.0f} Cr to ₹{CONFIG['turnover_max_cr']:.0f} Cr")
st.sidebar.write(f"Market cap band: ₹{CONFIG['mcap_min_cr']:.0f} Cr to ₹{CONFIG['mcap_max_cr']:.0f} Cr")
st.sidebar.write("Promoter holding soft test: ownership still scored, but not a hard pass blocker")
st.sidebar.write("Interest coverage floor: 4x")
st.sidebar.write("L3 relaxation test: if L2 and L5 pass, L3 needs 1-of-4 instead of 2-of-4")

st.sidebar.markdown("---")
st.sidebar.subheader("NSE price data")
uploaded_nse_file = st.sidebar.file_uploader(
    "Upload NSE EOD CSV (weekly bhavcopy)",
    type=["csv"],
    help="Download the equity bhavcopy from NSE on Friday night, then upload it here.",
)
pause_between_calls = st.sidebar.slider(
    "Pause between API calls (seconds)",
    min_value=0.0,
    max_value=1.0,
    value=0.2,
    step=0.1,
    help="yfinance/Yahoo may rate-limit if you make too many rapid requests.",
)

with st.expander("ScreenVerdict legend", expanded=False):
    st.markdown(
        """
| Verdict | Meaning |
|---|---|
| **PASS** | Passes all 5 layers; no data gaps. |
| **PASS (Data gaps present)** | Passes every layer where data is available; some layers untestable due to missing yfinance fields. |
| **FAIL (Genuine)** | Fails at least one layer where real data *is* available. |
| **FAIL (Insufficient data)** | Fewer than 3 layers could be tested. |
"""
    )

st.subheader("Fundamentals master (static upload)")
with st.expander("Show fundamentals_master.csv", expanded=False):
    fundamentals_df = load_fundamentals_master()
    if fundamentals_df.empty:
        st.info("fundamentals_master.csv not found or empty in the app directory.")
    else:
        st.write(f"Loaded {len(fundamentals_df)} stock(s) from fundamentals_master.csv")
        st.dataframe(fundamentals_df, use_container_width=True)

st.subheader("Stock master (sector & subsector mappings)")
with st.expander("Show stock_master.csv", expanded=False):
    stock_master_df = load_stock_master()
    if stock_master_df.empty:
        st.info("stock_master.csv not found or empty in the app directory.")
    else:
        st.write(f"Loaded {len(stock_master_df)} stock(s) from stock_master.csv")
        st.dataframe(stock_master_df, use_container_width=True)

st.subheader("NSE price data (uploaded weekly)")
with st.expander("Show uploaded NSE EOD CSV preview", expanded=False):
    if uploaded_nse_file is None:
        st.info("No NSE CSV uploaded yet. Use the file picker in the sidebar.")
        nse_prices_df = None
    else:
        try:
            nse_prices_df = pd.read_csv(uploaded_nse_file)
            st.write(f"NSE price file loaded with {len(nse_prices_df)} rows (all instruments).")
            st.dataframe(nse_prices_df.head(20), use_container_width=True)

            equity_universe_df = build_nse_equity_universe(nse_prices_df)
            st.markdown("**Equity universe after turnover-band filter**")
            if equity_universe_df.empty:
                st.warning("No equity symbols found in the configured turnover band.")
            else:
                st.write(
                    f"Universe has {len(equity_universe_df)} stock(s) after EQ + turnover filtering. "
                    "Showing top 50 by turnover."
                )
                st.dataframe(equity_universe_df.head(50), use_container_width=True)
        except Exception as e:
            st.error(f"Error reading NSE CSV: {e}")
            nse_prices_df = None

if st.button("Run live screen"):
    if uploaded_nse_file is not None:
        try:
            uploaded_nse_file.seek(0)
            raw_nse_df = pd.read_csv(uploaded_nse_file)
            equity_universe_df_local = build_nse_equity_universe(raw_nse_df)
        except Exception as e:
            st.error(f"Error rebuilding NSE equity universe: {e}")
            equity_universe_df_local = None
    else:
        equity_universe_df_local = None

    if equity_universe_df_local is not None and not equity_universe_df_local.empty:
        base_universe = equity_universe_df_local.head(int(max_stocks))
        universe_tickers = base_universe["Ticker"].astype(str).str.upper().tolist()
        tickers_to_screen = [f"{t}.NS" for t in universe_tickers]
        st.info(
            f"Using turnover-band universe: screening {len(tickers_to_screen)} stock(s) after EQ + turnover filtering."
        )
    else:
        tickers_to_screen = DEFAULT_UNIVERSE
        st.warning("No filtered NSE universe available; falling back to DEFAULT_UNIVERSE list.")

    fundamentals_master_df = load_fundamentals_master()
    stock_master_df = load_stock_master()
    rebuild_fundamentals_lookup(fundamentals_master_df)

    rows: List[Dict[str, Any]] = []
    with st.spinner("Fetching live market data..."):
        for ticker in tickers_to_screen:
            row = evaluate_stock(ticker)
            if row:
                rows.append(row)
            time.sleep(pause_between_calls)

    df = pd.DataFrame(rows)

    if not df.empty and stock_master_df is not None and not stock_master_df.empty:
        df = df.merge(
            stock_master_df[["Ticker", "Sector", "SubSector"]],
            on="Ticker",
            how="left",
            suffixes=("", "_stock"),
        )
        if "Sector_stock" in df.columns:
            df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
            df.drop(columns=["Sector_stock"], inplace=True)

    if not df.empty and fundamentals_master_df is not None and not fundamentals_master_df.empty:
        fundamentals_cols = [
            "Ticker",
            "Latest_Year",
            "ROE_Latest",
            "ROCE_Latest",
            "OPM_Latest",
            "NPM_Latest",
            "Revenue_CAGR_AllYears",
            "PAT_CAGR_AllYears",
            "ROCE_5Y_Avg",
            "ROE_5Y_Avg",
            "OPM_5Y_Avg",
            "OneOff_ROCE_Flag",
            "Asset_Quality_Risk_Flag",
            "Reg_Risk_Flag",
            "Gov_Risk_Flag",
        ]
        fundamentals_cols = [c for c in fundamentals_cols if c in fundamentals_master_df.columns]
        df = df.merge(
            fundamentals_master_df[fundamentals_cols],
            on="Ticker",
            how="left",
            suffixes=("", "_fund"),
        )

    if only_pass:
        if show_datagap:
            df = df[df["Pass"] == True]
        else:
            df = df[(df["Pass"] == True) & (df["ScreenVerdict"] == VERDICT_PASS)]

    if min_score > 0:
        df = df[df["Conviction"] >= min_score]

    verdict_order = {
        VERDICT_PASS: 0,
        VERDICT_PASS_DATAGAP: 1,
        VERDICT_FAIL_GENUINE: 2,
        VERDICT_FAIL_NODATA: 3,
    }
    df["_vsort"] = df["ScreenVerdict"].map(verdict_order).fillna(9).astype(int)
    df = df.sort_values(["Pass", "_vsort", "WeightedScore", "Conviction"], ascending=[False, True, False, False])
    df.drop(columns=["_vsort"], inplace=True)

    preferred_order = [
        "Ticker",
        "Sector",
        "ScreenVerdict",
        "Universe_MCap_Pass",
        "Price",
        "MCap_Cr",
        "PE",
        "PB",
        "PEG",
        "ROCE_pct",
        "ROE_pct",
        "ROA_pct",
        "OPM_pct",
        "RevGrowth_pct",
        "EarnGrowth_pct",
        "OCF_PAT",
        "FCFYield_pct",
        "InterestCoverage",
        "Insider_pct",
        "QualityScore_raw",
        "L1_Val",
        "L2_Prof",
        "L3_CF",
        "L4_Share",
        "L5_Forensic",
        "L1_DataMissing",
        "L2_DataMissing",
        "L3_DataMissing",
        "L4_DataMissing",
        "L5_DataMissing",
        "Conviction",
        "WeightedScore",
        "Pass",
        "HasFundamentals",
        "Latest_Year",
        "ROE_Latest",
        "ROCE_Latest",
        "OPM_Latest",
        "NPM_Latest",
        "Revenue_CAGR_AllYears",
        "PAT_CAGR_AllYears",
        "ROCE_5Y_Avg",
        "ROE_5Y_Avg",
        "OPM_5Y_Avg",
        "OneOff_ROCE_Flag",
        "Asset_Quality_Risk_Flag",
        "Reg_Risk_Flag",
        "Gov_Risk_Flag",
        "Error",
        "SubSector",
    ]
    existing_cols = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in existing_cols]
    df = df[existing_cols + remaining_cols]

    total = len(df)
    n_pass = (df["ScreenVerdict"] == VERDICT_PASS).sum()
    n_datagap = (df["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum()
    n_genuine = (df["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum()
    n_nodata = (df["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum()

    st.success(f"Screen complete — {total} stock(s) shown")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PASS", int(n_pass))
    c2.metric("PASS (Data gaps)", int(n_datagap))
    c3.metric("FAIL (Genuine)", int(n_genuine))
    c4.metric("FAIL (No data)", int(n_nodata))

    st.dataframe(df, use_container_width=True)
    st.download_button(
        "Download CSV",
        data=df.to_csv(index=False),
        file_name="100x_screener_fix1_ownership_soft_results.csv",
        mime="text/csv",
    )
else:
    st.info("Click **Run live screen** to start.")
