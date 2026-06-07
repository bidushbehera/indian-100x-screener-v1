import time
from typing import Dict, Any, List

import pandas as pd
import streamlit as st
import yfinance as yf

# Placeholder for NSE price data (uploaded weekly as CSV)
nse_prices_df = None
equity_universe_df = None
fundamentals_lookup = {}

st.sidebar.caption(f"yfinance version: {yf.__version__}")

# -----------------------------
# PAGE CONFIG & TITLE
# -----------------------------
st.set_page_config(
    page_title="100X Screener V1 – Indian Equities",
    layout="wide",
)

st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "V1 = Single-page Streamlit app using free Yahoo Finance data via yfinance. "
    "Acts as a narrowing engine, not a buy/sell signal."
)


# -----------------------------
# V1 SCOPE (FOR USER CLARITY)
# -----------------------------
with st.expander("What this V1 actually does / does NOT do", expanded=False):
    st.markdown(
        """
- **Implements (V1 reality):**
  - Screens a **small list of NSE stocks** using `yfinance.Ticker.info`.[^yfin]
  - Computes L1–L5, a Conviction score, and a WeightedScore.
  - Displays results in a table with CSV download.

- **Does *not* implement (future versions only):**
  - Watchlist persistence or score history.
  - Alerts (email / Telegram / WhatsApp).
  - Backtests (approximate or point-in-time).
  - Pledge-level shareholding data or detailed promoter analytics.
  - Alternative data providers beyond `yfinance`.

[^yfin]: `yfinance` is an unofficial wrapper around Yahoo Finance; coverage and reliability, especially for Indian fundamentals and shareholding data, are limited.[^yfin_ref]

"""
    )

# Simple footnote pointing to general yfinance references
st.markdown(
    "<sub>[^yfin_ref]: See the yfinance project docs and community guides for "
    "details and limitations on supported regions and fields.</sub>",
    unsafe_allow_html=True,
)


# -----------------------------
# CONFIGURATION
# -----------------------------
DEFAULT_UNIVERSE = [
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

# Thresholds are kept close to your original spec
CONFIG = {
    # Valuation
    "pe_max": 20.0,
    "peg_max": 1.0,
    "ev_ebitda_max": 12.0,
    "pb_max": 3.0,
    "mcap_min_cr": 200.0,
    "mcap_max_cr": 5000.0,
    # Profitability
    "roce_min": 0.20,   # 20%
    "roe_min": 0.18,    # 18%
    "roa_min": 0.10,    # 10%
    "opm_min": 0.15,    # 15%
    "rev_growth_min": 0.15,   # 15% (fraction)
    "earn_growth_min": 0.20,  # 20% (fraction)
    # Cash flow / balance sheet
    "ocf_pat_min": 0.8,
    "fcf_yield_min": 0.03,  # 3% (fraction of market cap)
    "de_max": 0.5,          # 0.5x debt/equity
    # Ownership
    "insider_min": 0.40,    # 40% (fraction)
    # Forensic quality (approximate Piotroski-style)
    "quality_min_raw": 5,   # out of 7 signals (see approx_quality_score)
}


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def safe(info: Dict[str, Any], key: str, default=None):
    """Robustly fetch a field from yfinance .info."""
    v = info.get(key, default)
    if v in (None, "N/A", "NaN"):
        return default
    return v


def approx_quality_score(info: Dict[str, Any]) -> int:
    """

def parse_percent_or_float(value):
    # Convert '22%' or 22 into a fraction like 0.22. Return None if not parseable.
    if value is None or pd.isna(value):
        return None

    # Handle strings
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Strip trailing %
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            num = float(text)
        except Exception:
            return None
    else:
        # Already numeric
        try:
            num = float(value)
        except Exception:
            return None

    # If it looks like a percentage (e.g. 22), convert to fraction
    if num > 1.5:
        return num / 100.0
    return num

    Approximate Piotroski-like forensic quality score using *only* snapshot fields
    available in yfinance.info.

    IMPORTANT (V1 honesty):
    - This is NOT a true Piotroski F-score.
    - True Piotroski requires year-over-year changes in profitability, leverage,
      liquidity, and efficiency using historical statements.
    - Here we use 7 static signals as a rough proxy.

    Signals (0/1 each, max 7):
    1. Net income > 0
    2. Operating cashflow > 0
    3. ROA > 5%
    4. OCF > Net income
    5. Long-term debt / total assets < 0.3
    6. Current ratio > 1.5
    7. Gross margin & revenue growth both healthy

    We combine margin and growth into a single point to keep max at 7.
    """

    score = 0

    ni = safe(info, "netIncomeToCommon") or 0
    ocf = safe(info, "operatingCashflow") or 0
    roa = safe(info, "returnOnAssets") or 0  # fraction
    ltd = safe(info, "longTermDebt") or 0
    ta = safe(info, "totalAssets") or 0
    cr = safe(info, "currentRatio") or 0
    gm = safe(info, "grossMargins") or 0     # fraction
    rg = safe(info, "revenueGrowth") or 0    # fraction

    # 1. Net income > 0
    if ni > 0:
        score += 1

    # 2. Operating cashflow > 0
    if ocf > 0:
        score += 1

    # 3. ROA > 5%
    if roa and roa > 0.05:
        score += 1

    # 4. OCF > net income
    if ocf > ni > 0:
        score += 1

    # 5. Long-term debt / total assets < 0.3
    if ta > 0 and (ltd / ta) < 0.3:
        score += 1

    # 6. Current ratio > 1.5
    if cr and cr > 1.5:
        score += 1

    # 7. Gross margin & revenue growth both healthy → 1 combined point
    if gm and gm > 0.2 and rg and rg > 0:
        score += 1

    return score  # 0–7

def load_fundamentals_master() -> pd.DataFrame:
    """
    Load fundamentals_master.csv from the app directory.

    Returns an empty DataFrame if the file is missing or unreadable.
    """
    try:
        df = pd.read_csv("fundamentals_master.csv")
        return df
    except Exception as e:
        st.warning(f"Could not load fundamentals_master.csv: {e}")
        return pd.DataFrame()

def load_stock_master() -> pd.DataFrame:
    """
    Load stock_master.csv from the app directory.

    Returns an empty DataFrame if the file is missing or unreadable.
    """
    try:
        df = pd.read_csv("stock_master.csv")
        return df
    except Exception as e:
        st.warning(f"Could not load stock_master.csv: {e}")
        return pd.DataFrame()

def rebuild_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> None:
    """
    Rebuild the in-memory fundamentals_lookup dictionary
    from fundamentals_master_df. Keys are uppercase tickers.
    """
    global fundamentals_lookup
    fundamentals_lookup = {}
    if fundamentals_master_df is None or fundamentals_master_df.empty:
        return

    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    fundamentals_lookup = {
        row["TickerKey"]: row
        for _, row in tmp.iterrows()
    }

def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    """
    From the raw NSE bhavcopy data, build a clean equity universe.

    - Keeps only cash equities (FinInstrmTp == 'STK')
    - Keeps only common equity series (SctySrs == 'EQ')
    - Returns a small table with ticker, series, close, volume, and turnover
    """
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()

    df = nse_df.copy()

    # Basic safety: ensure required columns exist
    required_cols = ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"NSE CSV is missing required column: {col}")
            return pd.DataFrame()

    # Keep only stock instruments
    df = df[df["FinInstrmTp"] == "STK"]

    # Keep only equity series (EQ). Other series like GB (gold bonds) are dropped.
    df = df[df["SctySrs"] == "EQ"]

    # If nothing left, return empty
    if df.empty:
        return pd.DataFrame()

    # Select and rename the key fields
    df = df[["TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol", "TtlTrfVal"]].copy()
    df = df.rename(
        columns={
            "TckrSymb": "Ticker",
            "SctySrs": "Series",
            "ClsPric": "Close",
            "TtlTradgVol": "Volume",
            "TtlTrfVal": "Turnover"
        }
    )

    # Optional: sort by turnover descending to see the most liquid names first
    df = df.sort_values("Turnover", ascending=False).reset_index(drop=True)

    return df

def evaluate_stock(ticker: str) -> Dict[str, Any]:
    """
    Evaluate a single stock.

    - Uses yfinance.Ticker.info for all fields (free, unofficial API; fragile for India).
    - Computes L1–L5, Conviction, WeightedScore, and final Pass flag.
    - Returns a dict row; on hard failures, returns a row with Pass=False and an Error field.
    """

    try:
        yf_ticker = yf.Ticker(ticker)
        # Look up any precomputed fundamentals row from fundamentals_master
        base_ticker = ticker.replace(".NS", "").upper()
        fund_row = fundamentals_lookup.get(base_ticker)
        info = yf_ticker.info

        # -------------
        # Raw fields
        # -------------
        pe = safe(info, "trailingPE")
        pb = safe(info, "priceToBook")
        ev_ebitda = safe(info, "enterpriseToEbitda")

        roe = safe(info, "returnOnEquity")      # fraction
        roa = safe(info, "returnOnAssets")      # fraction
        opm = safe(info, "operatingMargins")    # fraction
        revg = safe(info, "revenueGrowth")      # fraction
        earng = safe(info, "earningsGrowth")    # fraction (e.g., 0.2 = 20%)

        fcf = safe(info, "freeCashflow")
        ocf = safe(info, "operatingCashflow")
        ni = safe(info, "netIncomeToCommon")

        de = safe(info, "debtToEquity")         # assume already a ratio (e.g., 0.5, 1.2)
        insider = safe(info, "heldPercentInsiders")  # fraction, where available

        mcap_raw = safe(info, "marketCap") or 0
        price = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector = safe(info, "sector", "N/A")

        ebit = safe(info, "ebit")
        ta = safe(info, "totalAssets")
        current_liab = safe(info, "totalCurrentLiabilities")

        # -------------
        # Derived metrics
        # -------------
        # Market cap in INR crores (assuming Yahoo uses local currency for NSE tickers)
        mcap_cr = mcap_raw / 1e7 if mcap_raw else None

        # ROCE approximation using EBIT / (Total Assets - Current Liabilities)
        roce = None
        if ebit and ta and current_liab is not None:
            capital_employed = ta - current_liab
            if capital_employed > 0:
                roce = ebit / capital_employed

        # PEG: use earningsGrowth as fraction and convert to percent for classic PEG definition.
        peg = None
        if pe and earng and earng > 0:
            # earnings growth (in %) = earng * 100
            peg = pe / (earng * 100.0)

        # OCF / PAT
        ocf_pat = None
        if ocf and ni and ni > 0:
            ocf_pat = ocf / ni

        # FCF yield (fraction of market cap)
        fcf_yield = None
        if fcf and mcap_raw:
            fcf_yield = fcf / mcap_raw

        # Debt/Equity: assume data is already ratio; clamp absurdly large values
        de_ratio = None
        if de is not None and de >= 0:
            de_ratio = float(de)
            if de_ratio > 10:
                # Treat extreme numbers as unreliable; mark as None so L3 doesn't rely on them.
                de_ratio = None

        # Approximate forensic / quality score
        quality_raw = approx_quality_score(info)

        # -------------
        # L1–L5 PASS/FAIL
        # -------------
        l1_val = sum(
            [
                pe is not None and pe < CONFIG["pe_max"],
                peg is not None and peg < CONFIG["peg_max"],
                ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
                pb is not None and pb < CONFIG["pb_max"],
                mcap_cr is not None
                and CONFIG["mcap_min_cr"] <= mcap_cr <= CONFIG["mcap_max_cr"],
            ]
        ) >= 3

        # -----------------------------
        # Override L2 inputs with Excel fundamentals (if available)
        # -----------------------------
        if fund_row is not None:
            # ROE override
            if "ROE_Latest" in fund_row.index:
                roe_parsed = parse_percent_or_float(fund_row["ROE_Latest"])
                if roe_parsed is not None:
                    roe = roe_parsed

            # ROCE override
            if "ROCE_Latest" in fund_row.index:
                roce_parsed = parse_percent_or_float(fund_row["ROCE_Latest"])
                if roce_parsed is not None:
                    roce = roce_parsed

            # OPM override
            if "OPM_Latest" in fund_row.index:
                opm_parsed = parse_percent_or_float(fund_row["OPM_Latest"])
                if opm_parsed is not None:
                    opm = opm_parsed

            # Revenue growth override (use CAGR)
            if "Revenue_CAGR_AllYears" in fund_row.index:
                revg_parsed = parse_percent_or_float(fund_row["Revenue_CAGR_AllYears"])
                if revg_parsed is not None:
                    revg = revg_parsed

            # Earnings growth override (use PAT CAGR)
            if "PAT_CAGR_AllYears" in fund_row.index:
                earng_parsed = parse_percent_or_float(fund_row["PAT_CAGR_AllYears"])
                if earng_parsed is not None:
                    earng = earng_parsed
        
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

        # L4: ownership quality (proxy using heldPercentInsiders only)
        l4_share = insider is not None and insider > CONFIG["insider_min"]

        # L5: forensic quality using approximate Piotroski-style score
        l5_forensic = quality_raw >= CONFIG["quality_min_raw"]

        conviction = sum([l1_val, l2_prof, l3_cf, l4_share, l5_forensic])

        final_pass = bool(l2_prof and l5_forensic and conviction >= 4)

        # -------------
        # WEIGHTED SCORE
        # -------------
        weighted_score = 0

        # Valuation — 20 points
        weighted_score += 5 if pe is not None and pe < 20 else 0
        weighted_score += 5 if peg is not None and peg < 1 else 0
        weighted_score += 5 if ev_ebitda is not None and ev_ebitda < 12 else 0
        weighted_score += 3 if pb is not None and pb < 3 else 0
        weighted_score += 2 if (
            mcap_cr is not None and 200 <= mcap_cr <= 5000
        ) else 0

        # Profitability — 30 points
        weighted_score += 8 if roce is not None and roce > 0.20 else 0
        weighted_score += 6 if roe is not None and roe > 0.18 else 0
        weighted_score += 4 if roa is not None and roa > 0.10 else 0
        weighted_score += 4 if opm is not None and opm > 0.15 else 0
        weighted_score += 4 if revg is not None and revg > 0.15 else 0
        weighted_score += 4 if earng is not None and earng > 0.20 else 0

        # Cash flow / balance sheet — 20 points
        weighted_score += 8 if ocf_pat is not None and ocf_pat > 0.8 else 0
        weighted_score += 6 if fcf_yield is not None and fcf_yield > 0.03 else 0
        weighted_score += 6 if de_ratio is not None and de_ratio < 0.5 else 0

        # Ownership — 10 points (proxy only)
        weighted_score += 5 if l4_share else 0

        # Forensic quality — 20 points
        # Scale raw 0–7 score into 0–10 points (rounded).
        quality_points = round(10 * quality_raw / 7) if quality_raw is not None else 0
        if quality_points > 10:
            quality_points = 10
        weighted_score += quality_points

        return {
            "Ticker": ticker.replace(".NS", ""),
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
            "Insider_pct": round(insider * 100, 1) if insider is not None else None,
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
            "Insider_pct": None,
            "Piotroski": None,
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
            "Error": str(e),
        }


def run_screen(
    universe: List[str], min_conviction: int, only_pass: bool, pause_seconds: float = 0.2
) -> pd.DataFrame:
    """
    Run the live screen over a small universe.

    NOTE:
    - This is designed for a **small** list of tickers in V1.
    - For larger universes (NSE 500), this will be too slow and may hit Yahoo rate limits.
    """
    rows = []
    for ticker in universe:
        row = evaluate_stock(ticker)
        rows.append(row)
        time.sleep(pause_seconds)

        df = pd.DataFrame(rows)

    if only_pass:
        df = df[df["Pass"] == True]

    # Only filter by conviction when min_conviction > 0
    if min_conviction > 0:
        df = df[df["Conviction"] >= min_conviction]

    df = df.sort_values(
        ["Pass", "WeightedScore", "Conviction"],
        ascending=[False, False, False],
    )

    return df.reset_index(drop=True)

# -----------------------------
# SIDEBAR CONTROLS
# -----------------------------st.sidebar.header("Controls")
min_score = st.sidebar.slider("Minimum conviction score", 1, 5, 4)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)

max_stocks = st.sidebar.number_input(
    "Max stocks to screen (top by NSE turnover)",
    min_value=10,
    max_value=500,
    value=50,
    step=10
)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE price data")

uploaded_nse_file = st.sidebar.file_uploader(
    "Upload NSE EOD CSV (weekly bhavcopy)",
    type=["csv"],
    help="Download the equity bhavcopy from NSE on Friday night, then upload it here."
)
pause_between_calls = st.sidebar.slider(
    "Pause between API calls (seconds)",
    min_value=0.0,
    max_value=1.0,
    value=0.2,
    step=0.1,
    help=(
        "yfinance/Yahoo may rate-limit if you make too many rapid requests. "
        "For small universes this can be set low; for larger ones, keep some pause."
    ),
)

st.sidebar.write(f"Universe size (V1 fixed): {len(DEFAULT_UNIVERSE)} tickers")

# -----------------------------
# FUNDAMENTALS MASTER PREVIEW
# -----------------------------
st.subheader("Fundamentals master (static upload)")
with st.expander("Show fundamentals_master.csv", expanded=False):
    fundamentals_df = load_fundamentals_master()
    if fundamentals_df.empty:
        st.info("fundamentals_master.csv not found or empty in the app directory.")
    else:
        st.write(f"Loaded {len(fundamentals_df)} stock(s) from fundamentals_master.csv")
        st.dataframe(fundamentals_df, use_container_width=True)

# -----------------------------
# STOCK MASTER PREVIEW
# -----------------------------
st.subheader("Stock master (sector & subsector mappings)")
with st.expander("Show stock_master.csv", expanded=False):
    stock_master_df = load_stock_master()
    if stock_master_df.empty:
        st.info("stock_master.csv not found or empty in the app directory.")
    else:
        st.write(f"Loaded {len(stock_master_df)} stock(s) from stock_master.csv")
        st.dataframe(stock_master_df, use_container_width=True)

# -----------------------------
# NSE PRICE CSV PREVIEW
# -----------------------------
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

            # Build an equity-only universe from the raw NSE data
            equity_universe_df = build_nse_equity_universe(nse_prices_df)
            st.markdown("**Equity universe (FinInstrmTp == 'STK' and SctySrs == 'EQ')**")
            if equity_universe_df.empty:
                st.warning("No equity symbols (EQ series) found in this NSE file.")
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
    # Rebuild NSE equity universe from the uploaded file (do not rely on preview state)
    if uploaded_nse_file is not None:
        try:
            # Reset file pointer because it has already been read in the preview
            uploaded_nse_file.seek(0)
            raw_nse_df = pd.read_csv(uploaded_nse_file)
            equity_universe_df_local = build_nse_equity_universe(raw_nse_df)
        except Exception as e:
            st.error(f"Error rebuilding NSE equity universe: {e}")
            equity_universe_df_local = None
    else:
        equity_universe_df_local = None

    # Decide which universe to use
    if equity_universe_df_local is not None and not equity_universe_df_local.empty:
        # Use the top N stocks by turnover from the NSE equity universe
        base_universe = equity_universe_df_local.head(int(max_stocks))
        universe_tickers = base_universe["Ticker"].astype(str).str.upper().tolist()
        tickers_to_screen = [f"{t}.NS" for t in universe_tickers]
        st.info(f"Using NSE equity universe: screening top {len(tickers_to_screen)} stock(s) by turnover.")
    else:
        # Fallback to the old hard-coded default universe
        tickers_to_screen = DEFAULT_UNIVERSE
        st.warning("No NSE equity universe available; falling back to DEFAULT_UNIVERSE list.")

    # Load static master data
    fundamentals_master_df = load_fundamentals_master()
    stock_master_df = load_stock_master()
    rebuild_fundamentals_lookup(fundamentals_master_df)
    ...

    # Rebuild lookup dictionary from fundamentals_master (keyed by base ticker)
    rebuild_fundamentals_lookup(fundamentals_master_df)
    if fundamentals_master_df is not None and not fundamentals_master_df.empty:
        tmp = fundamentals_master_df.copy()
        # Use uppercase ticker keys so lookups are case-insensitive
        tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
        fundamentals_lookup = {
            row["TickerKey"]: row
            for _, row in tmp.iterrows()
        }
        
    rows = []
    with st.spinner("Fetching live market data..."):
        for ticker in tickers_to_screen:
            row = evaluate_stock(ticker)
            if row:
                rows.append(row)
            time.sleep(0.2)

    df = pd.DataFrame(rows)
        # Enrich with stock_master sector/subsector if available
    if not df.empty and stock_master_df is not None and not stock_master_df.empty:
        # Merge on Ticker
        df = df.merge(
            stock_master_df[["Ticker", "Sector", "SubSector"]],
            on="Ticker",
            how="left",
            suffixes=("", "_stock")
        )
        # Prefer Sector/SubSector from stock_master where present
        if "Sector_stock" in df.columns:
            df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
            df.drop(columns=["Sector_stock"], inplace=True)

        # Enrich with fundamentals_master metrics if available
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
        # Only keep columns that actually exist in the CSV
        fundamentals_cols = [c for c in fundamentals_cols if c in fundamentals_master_df.columns]

        df = df.merge(
            fundamentals_master_df[fundamentals_cols],
            on="Ticker",
            how="left",
            suffixes=("", "_fund")
        )
        
    if only_pass:
        df = df[df["Pass"] == True]
    df = df[df["Conviction"] >= min_score].sort_values(
        ["Pass", "WeightedScore", "Conviction"],
        ascending=[False, False, False]
    )

    # Optional: basic column reordering so new fundamentals are grouped
    preferred_order = [
        "Ticker",
        "Sector",
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
        "Insider_pct",
        "QualityScore_raw",
        "L1_Val",
        "L2_Prof",
        "L3_CF",
        "L4_Share",
        "L5_Forensic",
        "Conviction",
        "WeightedScore",
        "Pass",
        # Fundamentals master fields
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
    # Keep only columns that actually exist, plus any extra columns at the end
    existing_cols = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in existing_cols]
    df = df[existing_cols + remaining_cols]
    
    st.success(f"Found {len(df)} stocks")
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "Download CSV",
        data=df.to_csv(index=False),
        file_name="100x_screener_v3_results.csv",
        mime="text/csv"
    )
else:
    st.info("Click **Run live screen** to start.")
