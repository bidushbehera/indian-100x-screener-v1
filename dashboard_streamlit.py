import time
from typing import Dict, Any, List, Optional

import pandas as pd
import streamlit as st
import yfinance as yf

# -----------------------------
# GLOBALS
# -----------------------------
nse_prices_df = None
equity_universe_df = None
fundamentals_lookup: Dict[str, Any] = {}
stock_master_lookup: Dict[str, Any] = {}
shareholding_lookup: Dict[str, Any] = {}

# Set this to your actual NSE shareholding CSV filename
SHAREHOLDING_FILE = "CF-Shareholding-Pattern-equities-07-Jun-2026.csv"

st.sidebar.caption(f"yfinance version: {yf.__version__}")

# -----------------------------
# PAGE CONFIG & TITLE
# -----------------------------
st.set_page_config(
    page_title="100X Screener V1 - Indian Equities",
    layout="wide",
)

st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "V1 = Single-page Streamlit app using free Yahoo Finance data via yfinance, "
    "plus static fundamentals and NSE shareholding pattern data. "
    "Acts as a narrowing engine, not a buy/sell signal."
)

# -----------------------------
# V1 SCOPE
# -----------------------------
with st.expander("What this V1 actually does / does NOT do", expanded=False):
    st.markdown(
        """
- **Implements (V1 reality):**
  - Screens NSE stocks using `yfinance.Ticker.info`.
  - Uses `fundamentals_master.csv` to override L2 profitability inputs.
  - Uses NSE shareholding pattern CSV to drive L4 ownership quality.
  - Computes L1–L5, Conviction score, and WeightedScore.
  - Displays results in a table with CSV download.

- **Does *not* implement (future versions only):**
  - Watchlist persistence or score history.
  - Alerts (email / Telegram / WhatsApp).
  - Backtests (approximate or point-in-time).
  - Full promoter pledge / encumbrance analytics from XBRL parsing.
  - Alternative data providers beyond `yfinance`.
"""
    )

# -----------------------------
# CONFIGURATION
# -----------------------------
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
    # Valuation
    "pe_max": 20.0,
    "peg_max": 1.0,
    "ev_ebitda_max": 12.0,
    "pb_max": 3.0,
    "mcap_min_cr": 200.0,
    "mcap_max_cr": 5000.0,

    # Profitability
    "roce_min": 0.20,
    "roe_min": 0.18,
    "roa_min": 0.10,
    "opm_min": 0.15,
    "rev_growth_min": 0.15,
    "earn_growth_min": 0.20,

    # Cash flow / balance sheet
    "ocf_pat_min": 0.8,
    "fcf_yield_min": 0.03,
    "de_max": 0.5,

    # Ownership - NSE shareholding based
    "promoter_min": 0.40,  # 40%
    "ownership_total_tolerance": 0.5,  # total should be between 99.5 and 100.5

    # Forensic quality
    "quality_min_raw": 5,
}

# -----------------------------
# HELPERS
# -----------------------------
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

    if num > 1.5:
        return num / 100.0
    return num


def parse_float(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        return float(str(value).strip())
    except Exception:
        return None


def normalize_company_name(name: str) -> str:
    if name is None:
        return ""
    text = str(name).upper().strip()
    replacements = {
        " LIMITED": "",
        " LTD.": "",
        " LTD": "",
        " LIMITED.": "",
        "&": "AND",
        ",": "",
        ".": "",
        "'": "",
        "-": " ",
        "  ": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


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


def load_shareholding_master() -> pd.DataFrame:
    try:
        df = pd.read_csv(SHAREHOLDING_FILE)
        return df
    except Exception as e:
        st.warning(f"Could not load {SHAREHOLDING_FILE}: {e}")
        return pd.DataFrame()


def rebuild_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> None:
    global fundamentals_lookup
    fundamentals_lookup = {}

    if fundamentals_master_df is None or fundamentals_master_df.empty:
        return

    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    fundamentals_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def rebuild_stock_master_lookup(stock_master_df: pd.DataFrame) -> None:
    global stock_master_lookup
    stock_master_lookup = {}

    if stock_master_df is None or stock_master_df.empty:
        return

    tmp = stock_master_df.copy()
    if "Ticker" in tmp.columns:
        tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
        stock_master_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def prepare_shareholding_master(shareholding_df: pd.DataFrame) -> pd.DataFrame:
    if shareholding_df is None or shareholding_df.empty:
        return pd.DataFrame()

    df = shareholding_df.copy()

    expected_cols = [
        "COMPANY",
        "PROMOTER & PROMOTER GROUP (A)",
        "PUBLIC (B)",
        "SHARES HELD BY EMPLOYEE TRUSTS (C2)",
        "STATUS",
        "AS ON DATE",
        "SUBMISSION DATE",
        "REVISION DATE",
        "ACTION",
        "BROADCAST DATE/TIME",
        "EXCHANGE DISSEMINATION TIME",
        "TIME TAKEN",
    ]

    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        st.warning(f"Shareholding CSV missing columns: {missing}")
        return pd.DataFrame()

    df["PromoterPct_NSE"] = df["PROMOTER & PROMOTER GROUP (A)"].apply(parse_float)
    df["PublicPct_NSE"] = df["PUBLIC (B)"].apply(parse_float)
    df["EmployeeTrustPct_NSE"] = df["SHARES HELD BY EMPLOYEE TRUSTS (C2)"].apply(parse_float)

    df["OwnershipTotalPct"] = (
        df["PromoterPct_NSE"].fillna(0)
        + df["PublicPct_NSE"].fillna(0)
        + df["EmployeeTrustPct_NSE"].fillna(0)
    )

    tol = CONFIG["ownership_total_tolerance"]
    df["OwnershipDataValid"] = (
        df["PromoterPct_NSE"].notna()
        & df["PublicPct_NSE"].notna()
        & df["EmployeeTrustPct_NSE"].notna()
        & df["OwnershipTotalPct"].between(100 - tol, 100 + tol)
    )

    df["AsOnDate_dt"] = pd.to_datetime(df["AS ON DATE"], errors="coerce", dayfirst=True)
    df["SubmissionDate_dt"] = pd.to_datetime(df["SUBMISSION DATE"], errors="coerce", dayfirst=True)
    df["RevisionDate_dt"] = pd.to_datetime(df["REVISION DATE"], errors="coerce", dayfirst=True)

    df["IsRevised"] = df["STATUS"].astype(str).str.upper().eq("REVISED")
    df["CompanyKey"] = df["COMPANY"].apply(normalize_company_name)

    df = df.sort_values(
        by=["CompanyKey", "AsOnDate_dt", "RevisionDate_dt", "SubmissionDate_dt", "IsRevised"],
        ascending=[True, False, False, False, False]
    )

    latest = df.drop_duplicates(subset=["CompanyKey"], keep="first").copy()
    return latest


def rebuild_shareholding_lookup(shareholding_master_df: pd.DataFrame, stock_master_df: pd.DataFrame) -> None:
    global shareholding_lookup
    shareholding_lookup = {}

    if shareholding_master_df is None or shareholding_master_df.empty:
        return

    company_map = {}
    if stock_master_df is not None and not stock_master_df.empty:
        tmp = stock_master_df.copy()

        if "Ticker" in tmp.columns and "CompanyName" in tmp.columns:
            for _, row in tmp.iterrows():
                ticker = str(row["Ticker"]).upper()
                company_key = normalize_company_name(row["CompanyName"])
                if company_key:
                    company_map[ticker] = company_key

        elif "Ticker" in tmp.columns and "Company" in tmp.columns:
            for _, row in tmp.iterrows():
                ticker = str(row["Ticker"]).upper()
                company_key = normalize_company_name(row["Company"])
                if company_key:
                    company_map[ticker] = company_key

    for _, row in shareholding_master_df.iterrows():
        company_key = row["CompanyKey"]
        shareholding_lookup[company_key] = row

    # Also build a ticker-based lookup if stock_master has mapping
    for ticker, company_key in company_map.items():
        row = shareholding_lookup.get(company_key)
        if row is not None:
            shareholding_lookup[ticker] = row


def get_shareholding_row(base_ticker: str) -> Optional[pd.Series]:
    base_ticker = str(base_ticker).upper()

    # First try ticker-based lookup
    row = shareholding_lookup.get(base_ticker)
    if row is not None:
        return row

    # Then try stock_master-based company name
    stock_row = stock_master_lookup.get(base_ticker)
    if stock_row is not None:
        company_name = None
        if "CompanyName" in stock_row.index:
            company_name = stock_row["CompanyName"]
        elif "Company" in stock_row.index:
            company_name = stock_row["Company"]

        if company_name:
            company_key = normalize_company_name(company_name)
            return shareholding_lookup.get(company_key)

    return None


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
    df = df.sort_values("Turnover", ascending=False).reset_index(drop=True)
    return df


def evaluate_stock(ticker: str) -> Dict[str, Any]:
    try:
        yf_ticker = yf.Ticker(ticker)
        base_ticker = ticker.replace(".NS", "").upper()

        fund_row = fundamentals_lookup.get(base_ticker)
        share_row = get_shareholding_row(base_ticker)

        info = yf_ticker.info

        # Raw fields
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
        yahoo_insider = safe(info, "heldPercentInsiders")

        mcap_raw = safe(info, "marketCap") or 0
        price = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector = safe(info, "sector", "N/A")

        ebit = safe(info, "ebit")
        ta = safe(info, "totalAssets")
        current_liab = safe(info, "totalCurrentLiabilities")

        # Derived
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

        # Shareholding data from NSE
        promoter_pct_nse = None
        public_pct_nse = None
        employee_trust_pct_nse = None
        ownership_total_pct = None
        ownership_data_valid = False
        shareholding_status = None
        shareholding_as_on_date = None
        shareholding_revision_date = None
        shareholding_action_link = None

        if share_row is not None:
            promoter_pct_nse = parse_float(share_row.get("PromoterPct_NSE"))
            public_pct_nse = parse_float(share_row.get("PublicPct_NSE"))
            employee_trust_pct_nse = parse_float(share_row.get("EmployeeTrustPct_NSE"))
            ownership_total_pct = parse_float(share_row.get("OwnershipTotalPct"))
            ownership_data_valid = bool(share_row.get("OwnershipDataValid", False))
            shareholding_status = share_row.get("STATUS")
            shareholding_as_on_date = share_row.get("AS ON DATE")
            shareholding_revision_date = share_row.get("REVISION DATE")
            shareholding_action_link = share_row.get("ACTION")

        # L1
        l1_val = sum(
            [
                pe is not None and pe < CONFIG["pe_max"],
                peg is not None and peg < CONFIG["peg_max"],
                ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
                pb is not None and pb < CONFIG["pb_max"],
                mcap_cr is not None and CONFIG["mcap_min_cr"] <= mcap_cr <= CONFIG["mcap_max_cr"],
            ]
        ) >= 3

        # L2 overrides from fundamentals_master
        if fund_row is not None:
            if "ROE_Latest" in fund_row.index:
                parsed = parse_percent_or_float(fund_row["ROE_Latest"])
                if parsed is not None:
                    roe = parsed

            if "ROCE_Latest" in fund_row.index:
                parsed = parse_percent_or_float(fund_row["ROCE_Latest"])
                if parsed is not None:
                    roce = parsed

            if "OPM_Latest" in fund_row.index:
                parsed = parse_percent_or_float(fund_row["OPM_Latest"])
                if parsed is not None:
                    opm = parsed

            if "Revenue_CAGR_AllYears" in fund_row.index:
                parsed = parse_percent_or_float(fund_row["Revenue_CAGR_AllYears"])
                if parsed is not None:
                    revg = parsed

            if "PAT_CAGR_AllYears" in fund_row.index:
                parsed = parse_percent_or_float(fund_row["PAT_CAGR_AllYears"])
                if parsed is not None:
                    earng = parsed

        l2_prof = sum(
            [
                roce is not None and roce > CONFIG["roce_min"],
                roe is not None and roe > CONFIG["roe_min"],
                roa is not None and roa > CONFIG["roa_min"],
                opm is not None and opm > CONFIG["opm_min"],
                revg is not None and revg > CONFIG["rev_growth_min"],
                earng is not None and earng > CONFIG["earn_growth_min"],
            ]
        ) >= 4

        l3_cf = sum(
            [
                ocf_pat is not None and ocf_pat > CONFIG["ocf_pat_min"],
                fcf_yield is not None and fcf_yield > CONFIG["fcf_yield_min"],
                de_ratio is not None and de_ratio < CONFIG["de_max"],
            ]
        ) >= 2

        # L4 - now driven by NSE shareholding data
        l4_share = (
            ownership_data_valid
            and promoter_pct_nse is not None
            and promoter_pct_nse / 100.0 >= CONFIG["promoter_min"]
        )

        l5_forensic = quality_raw >= CONFIG["quality_min_raw"]

        conviction = sum([l1_val, l2_prof, l3_cf, l4_share, l5_forensic])
        final_pass = bool(l2_prof and l5_forensic and conviction >= 4)

        weighted_score = 0

        # Valuation - 20
        weighted_score += 5 if pe is not None and pe < 20 else 0
        weighted_score += 5 if peg is not None and peg < 1 else 0
        weighted_score += 5 if ev_ebitda is not None and ev_ebitda < 12 else 0
        weighted_score += 3 if pb is not None and pb < 3 else 0
        weighted_score += 2 if mcap_cr is not None and 200 <= mcap_cr <= 5000 else 0

        # Profitability - 30
        weighted_score += 8 if roce is not None and roce > 0.20 else 0
        weighted_score += 6 if roe is not None and roe > 0.18 else 0
        weighted_score += 4 if roa is not None and roa > 0.10 else 0
        weighted_score += 4 if opm is not None and opm > 0.15 else 0
        weighted_score += 4 if revg is not None and revg > 0.15 else 0
        weighted_score += 4 if earng is not None and earng > 0.20 else 0

        # Cash flow / balance sheet - 20
        weighted_score += 8 if ocf_pat is not None and ocf_pat > 0.8 else 0
        weighted_score += 6 if fcf_yield is not None and fcf_yield > 0.03 else 0
        weighted_score += 6 if de_ratio is not None and de_ratio < 0.5 else 0

        # Ownership - 10
        weighted_score += 10 if l4_share else 0

        # Forensic - 20
        quality_points = round(10 * quality_raw / 7) if quality_raw is not None else 0
        quality_points = min(quality_points, 10)
        weighted_score += quality_points

        return {
            "Ticker": base_ticker,
            "Sector": sector,
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
            "YahooInsider_pct": round(yahoo_insider * 100, 1) if yahoo_insider is not None else None,
            "PromoterPct_NSE": round(promoter_pct_nse, 2) if promoter_pct_nse is not None else None,
            "PublicPct_NSE": round(public_pct_nse, 2) if public_pct_nse is not None else None,
            "EmployeeTrustPct_NSE": round(employee_trust_pct_nse, 2) if employee_trust_pct_nse is not None else None,
            "OwnershipTotalPct": round(ownership_total_pct, 2) if ownership_total_pct is not None else None,
            "OwnershipDataValid": ownership_data_valid,
            "ShareholdingStatus": shareholding_status,
            "ShareholdingAsOnDate": shareholding_as_on_date,
            "ShareholdingRevisionDate": shareholding_revision_date,
            "ShareholdingActionLink": shareholding_action_link,
            "QualityScore_raw": quality_raw,
            "L1_Val": l1_val,
            "L2_Prof": l2_prof,
            "L3_CF": l3_cf,
            "L4_Share": l4_share,
            "L5_Forensic": l5_forensic,
            "Conviction": conviction,
            "WeightedScore": weighted_score,
            "Pass": final_pass,
            "HasFundamentals": fund_row is not None,
            "HasShareholdingData": share_row is not None,
            "Error": None,
        }

    except Exception as e:
        base_ticker = ticker.replace(".NS", "")
        return {
            "Ticker": base_ticker,
            "Sector": None,
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
            "YahooInsider_pct": None,
            "PromoterPct_NSE": None,
            "PublicPct_NSE": None,
            "EmployeeTrustPct_NSE": None,
            "OwnershipTotalPct": None,
            "OwnershipDataValid": False,
            "ShareholdingStatus": None,
            "ShareholdingAsOnDate": None,
            "ShareholdingRevisionDate": None,
            "ShareholdingActionLink": None,
            "QualityScore_raw": None,
            "L1_Val": False,
            "L2_Prof": False,
            "L3_CF": False,
            "L4_Share": False,
            "L5_Forensic": False,
            "Conviction": 0,
            "WeightedScore": 0,
            "Pass": False,
            "HasFundamentals": False,
            "HasShareholdingData": False,
            "Error": str(e),
        }


# -----------------------------
# SIDEBAR CONTROLS
# -----------------------------
st.sidebar.header("Controls")
min_score = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)

max_stocks = st.sidebar.number_input(
    "Max stocks to screen (top by NSE turnover)",
    min_value=10,
    max_value=500,
    value=50,
    step=10,
)

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
)

st.sidebar.write(f"Universe size (V1 fixed): {len(DEFAULT_UNIVERSE)} tickers")

# -----------------------------
# PREVIEWS
# -----------------------------
st.subheader("Fundamentals master")
with st.expander("Show fundamentals_master.csv", expanded=False):
    fundamentals_df = load_fundamentals_master()
    if fundamentals_df.empty:
        st.info("fundamentals_master.csv not found or empty.")
    else:
        st.write(f"Loaded {len(fundamentals_df)} row(s)")
        st.dataframe(fundamentals_df, use_container_width=True)

st.subheader("Stock master")
with st.expander("Show stock_master.csv", expanded=False):
    stock_master_df = load_stock_master()
    if stock_master_df.empty:
        st.info("stock_master.csv not found or empty.")
    else:
        st.write(f"Loaded {len(stock_master_df)} row(s)")
        st.dataframe(stock_master_df, use_container_width=True)

st.subheader("NSE shareholding master")
with st.expander(f"Show {SHAREHOLDING_FILE}", expanded=False):
    shareholding_raw_df = load_shareholding_master()
    if shareholding_raw_df.empty:
        st.info(f"{SHAREHOLDING_FILE} not found or empty.")
    else:
        shareholding_prepared_df = prepare_shareholding_master(shareholding_raw_df)
        st.write(f"Loaded {len(shareholding_raw_df)} raw row(s)")
        st.write(f"Prepared latest-shareholding table with {len(shareholding_prepared_df)} company row(s)")
        st.dataframe(
            shareholding_prepared_df[
                [
                    "COMPANY",
                    "PromoterPct_NSE",
                    "PublicPct_NSE",
                    "EmployeeTrustPct_NSE",
                    "OwnershipTotalPct",
                    "OwnershipDataValid",
                    "STATUS",
                    "AS ON DATE",
                    "REVISION DATE",
                ]
            ].head(50),
            use_container_width=True,
        )

st.subheader("NSE price data (uploaded weekly)")
with st.expander("Show uploaded NSE EOD CSV preview", expanded=False):
    if uploaded_nse_file is None:
        st.info("No NSE CSV uploaded yet. Use the sidebar uploader.")
        nse_prices_df = None
    else:
        try:
            nse_prices_df = pd.read_csv(uploaded_nse_file)
            st.write(f"NSE price file loaded with {len(nse_prices_df)} rows.")
            st.dataframe(nse_prices_df.head(20), use_container_width=True)

            equity_universe_df = build_nse_equity_universe(nse_prices_df)
            st.markdown("**Equity universe (FinInstrmTp == 'STK' and SctySrs == 'EQ')**")
            if equity_universe_df.empty:
                st.warning("No equity symbols found in this NSE file.")
            else:
                st.write(f"Equity universe has {len(equity_universe_df)} stock(s). Showing top 50 by turnover.")
                st.dataframe(equity_universe_df.head(50), use_container_width=True)
        except Exception as e:
            st.error(f"Error reading NSE CSV: {e}")
            nse_prices_df = None

# -----------------------------
# MAIN ACTION
# -----------------------------
if st.button("Run live screen"):
    # Rebuild price universe
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
        st.info(f"Using NSE equity universe: screening top {len(tickers_to_screen)} stock(s) by turnover.")
    else:
        tickers_to_screen = DEFAULT_UNIVERSE
        st.warning("No NSE equity universe available; falling back to DEFAULT_UNIVERSE.")

    # Load masters
    fundamentals_master_df = load_fundamentals_master()
    stock_master_df = load_stock_master()
    shareholding_raw_df = load_shareholding_master()
    shareholding_master_df = prepare_shareholding_master(shareholding_raw_df)

    rebuild_fundamentals_lookup(fundamentals_master_df)
    rebuild_stock_master_lookup(stock_master_df)
    rebuild_shareholding_lookup(shareholding_master_df, stock_master_df)

    rows = []
    with st.spinner("Fetching live market data..."):
        for ticker in tickers_to_screen:
            row = evaluate_stock(ticker)
            rows.append(row)
            time.sleep(pause_between_calls)

    df = pd.DataFrame(rows)

    # Enrich with stock master
    if not df.empty and stock_master_df is not None and not stock_master_df.empty:
        merge_cols = [c for c in ["Ticker", "Sector", "SubSector", "CompanyName", "Company"] if c in stock_master_df.columns]
        if "Ticker" in merge_cols:
            df = df.merge(
                stock_master_df[merge_cols],
                on="Ticker",
                how="left",
                suffixes=("", "_stock"),
            )
            if "Sector_stock" in df.columns:
                df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
                df.drop(columns=["Sector_stock"], inplace=True)

    # Enrich with fundamentals master
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
        df = df[df["Pass"] == True]
    if min_score > 0:
        df = df[df["Conviction"] >= min_score]

    df = df.sort_values(
        ["Pass", "WeightedScore", "Conviction"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    preferred_order = [
        "Ticker",
        "CompanyName",
        "Company",
        "Sector",
        "SubSector",
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
        "YahooInsider_pct",
        "PromoterPct_NSE",
        "PublicPct_NSE",
        "EmployeeTrustPct_NSE",
        "OwnershipTotalPct",
        "OwnershipDataValid",
        "ShareholdingStatus",
        "ShareholdingAsOnDate",
        "ShareholdingRevisionDate",
        "QualityScore_raw",
        "L1_Val",
        "L2_Prof",
        "L3_CF",
        "L4_Share",
        "L5_Forensic",
        "Conviction",
        "WeightedScore",
        "Pass",
        "HasFundamentals",
        "HasShareholdingData",
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
    ]

    existing_cols = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in existing_cols]
    df = df[existing_cols + remaining_cols]

    st.success(f"Found {len(df)} stocks")
    st.dataframe(df, use_container_width=True)

    bad_ownership_df = df[
        (df["HasShareholdingData"] == True) & (df["OwnershipDataValid"] == False)
    ] if not df.empty and "HasShareholdingData" in df.columns and "OwnershipDataValid" in df.columns else pd.DataFrame()

    with st.expander("Rows with shareholding-data anomalies", expanded=False):
        if bad_ownership_df.empty:
            st.info("No ownership anomalies in screened names.")
        else:
            st.warning("These names have shareholding data present, but the ownership totals failed validation.")
            st.dataframe(
                bad_ownership_df[
                    [
                        "Ticker",
                        "PromoterPct_NSE",
                        "PublicPct_NSE",
                        "EmployeeTrustPct_NSE",
                        "OwnershipTotalPct",
                        "OwnershipDataValid",
                        "ShareholdingStatus",
                        "ShareholdingAsOnDate",
                    ]
                ],
                use_container_width=True,
            )

    st.download_button(
        "Download CSV",
        data=df.to_csv(index=False),
        file_name="100x_screener_v4_step_7c_results.csv",
        mime="text/csv",
    )
else:
    st.info("Click **Run live screen** to start.")
