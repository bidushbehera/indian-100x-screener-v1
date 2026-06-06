import time
from typing import Dict, Any, List

import pandas as pd
import streamlit as st
import yfinance as yf

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


def evaluate_stock(ticker: str) -> Dict[str, Any]:
    """
    Evaluate a single stock.

    - Uses yfinance.Ticker.info for all fields (free, unofficial API; fragile for India).
    - Computes L1–L5, Conviction, WeightedScore, and final Pass flag.
    - Returns a dict row; on hard failures, returns a row with Pass=False and an Error field.
    """

    try:
        yf_ticker = yf.Ticker(ticker)
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
            "Error": None,
        }

    except Exception as e:
        # Fail-soft: still return a row so user can see the problem ticker.
        return {
            "Ticker": ticker.replace(".NS", ""),
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
            "QualityScore_raw": None,
            "L1_Val": False,
            "L2_Prof": False,
            "L3_CF": False,
            "L4_Share": False,
            "L5_Forensic": False,
            "Conviction": 0,
            "WeightedScore": 0,
            "Pass": False,
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
# -----------------------------
st.sidebar.header("Controls")

min_score = st.sidebar.slider("Minimum conviction score", 0, 5, 1)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)
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
# MAIN ACTION
# -----------------------------
if st.button("Run live screen"):
    with st.spinner("Fetching live market data from Yahoo Finance via yfinance..."):
        df_results = run_screen(
            universe=DEFAULT_UNIVERSE,
            min_conviction=min_score,
            only_pass=only_pass,
            pause_seconds=pause_between_calls,
        )

    st.success(f"Found {len(df_results)} stocks matching filters")

    # Highlight errors inline if any
    if "Error" in df_results.columns and df_results["Error"].notna().any():
        st.warning(
            "Some tickers returned errors from yfinance. Scroll right in the table "
            "to see the 'Error' column for details."
        )

    st.dataframe(df_results, use_container_width=True)

    st.download_button(
        "Download CSV",
        data=df_results.to_csv(index=False),
        file_name="100x_screener_v1_results.csv",
        mime="text/csv",
    )

else:
    st.info("Click **Run live screen** to start.")
