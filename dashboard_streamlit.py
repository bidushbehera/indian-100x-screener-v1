import time
from typing import Dict, Any, List, Optional

import pandas as pd
import streamlit as st
import yfinance as yf

# -----------------------------
# TICKER → NSE COMPANY NAME MAP
# Exact names as they appear in the CF-Shareholding-Pattern CSV (COMPANY column)
# Add more entries here as you expand the universe
# -----------------------------
TICKER_TO_COMPANY: Dict[str, str] = {
    "POLYCAB":   "Polycab India Limited",
    "TANLA":     "Tanla Platforms Limited",
    "KPITTECH":  "KPIT Technologies Limited",
    "CDSL":      "Central Depository Services (India) Limited",
    "CAMS":      "Computer Age Management Services Limited",
    "IRCTC":     "Indian Railway Catering And Tourism Corporation Limited",
    "CGPOWER":   "CG Power and Industrial Solutions Limited",
    "DEEPAKNTR": "Deepak Nitrite Limited",
    "OLECTRA":   "Olectra Greentech Limited",
    "LLOYDSME":  "Lloyds Metals And Energy Limited",
}

# NSE shareholding CSV exact column names (from CF-Shareholding-Pattern-equities CSV)
SH_COL_COMPANY   = "COMPANY"
SH_COL_PROMOTER  = "PROMOTER & PROMOTER GROUP (A)"
SH_COL_PUBLIC    = "PUBLIC (B)"
SH_COL_EMP_TRUST = "SHARES HELD BY EMPLOYEE TRUSTS (C2)"
SH_COL_STATUS    = "STATUS"
SH_COL_AS_ON     = "AS ON DATE"
SH_COL_REVISION  = "REVISION DATE"
SH_COL_ACTION    = "ACTION"

nse_prices_df      = None
equity_universe_df = None
fundamentals_lookup: Dict[str, Any]  = {}
shareholding_lookup: Dict[str, Dict] = {}

st.sidebar.caption(f"yfinance version: {yf.__version__}")

st.set_page_config(page_title="100X Screener V1 - Indian Equities", layout="wide")

st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "V1 = Single-page Streamlit app using free Yahoo Finance data via yfinance. "
    "Acts as a narrowing engine, not a buy/sell signal."
)

with st.expander("What this V1 actually does / does NOT do", expanded=False):
    st.markdown("""
- **Implements (V1 reality):**
  - Screens NSE stocks using `yfinance.Ticker.info` plus your curated `fundamentals_master.csv`.
  - L4 Ownership uses NSE official shareholding CSV (promoter ≥ 40%) when uploaded, else falls back to yfinance insiderHoldingsPercent.
  - Computes L1–L5, Conviction, WeightedScore, and a **ScreenVerdict** that distinguishes genuine failures from data-gap failures.
  - Displays results in a table with CSV download.

- **Does *not* implement (future versions only):**
  - Watchlist persistence or score history.
  - Alerts (email / Telegram / WhatsApp).
  - Backtests.
  - Pledge-level shareholding or detailed promoter analytics.
  - Alternative data providers beyond yfinance.
""")

st.markdown(
    "<sub>yfinance is an unofficial wrapper around Yahoo Finance; coverage and reliability "
    "especially for Indian fundamentals and shareholding data are limited. "
    "L4 Ownership is more reliable when you upload the NSE shareholding CSV.</sub>",
    unsafe_allow_html=True,
)

# -----------------------------
# CONFIGURATION
# -----------------------------
DEFAULT_UNIVERSE: List[str] = [
    "LLOYDSME.NS", "POLYCAB.NS", "DEEPAKNTR.NS", "CGPOWER.NS", "TANLA.NS",
    "KPITTECH.NS", "CDSL.NS", "CAMS.NS", "IRCTC.NS", "OLECTRA.NS",
]

CONFIG: Dict[str, Any] = {
    "pe_max":          20.0,
    "peg_max":          1.0,
    "ev_ebitda_max":   12.0,
    "pb_max":           3.0,
    "mcap_min_cr":    200.0,
    "mcap_max_cr":   5000.0,
    "roce_min":        0.20,
    "roe_min":         0.18,
    "roa_min":         0.10,
    "opm_min":         0.15,
    "rev_growth_min":  0.15,
    "earn_growth_min": 0.20,
    "ocf_pat_min":     0.80,
    "fcf_yield_min":   0.03,
    "de_max":          0.50,
    "promoter_min":    0.40,
    "insider_min":     0.40,
    "quality_min_raw":  5,
}

VERDICT_PASS         = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA  = "FAIL (Insufficient data)"


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def safe(info: Dict[str, Any], key: str, default=None):
    v = info.get(key, default)
    if v in (None, "N/A", "NaN"):
        return default
    return v


def parse_percent_or_float(value) -> Optional[float]:
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
    ni  = safe(info, "netIncomeToCommon") or 0
    ocf = safe(info, "operatingCashflow") or 0
    roa = safe(info, "returnOnAssets") or 0
    ltd = safe(info, "longTermDebt") or 0
    ta  = safe(info, "totalAssets") or 0
    cr  = safe(info, "currentRatio") or 0
    gm  = safe(info, "grossMargins") or 0
    rg  = safe(info, "revenueGrowth") or 0
    if ni > 0:                             score += 1
    if ocf > 0:                            score += 1
    if roa and roa > 0.05:                 score += 1
    if ocf > ni > 0:                       score += 1
    if ta > 0 and (ltd / ta) < 0.3:       score += 1
    if cr and cr > 1.5:                    score += 1
    if gm and gm > 0.2 and rg and rg > 0: score += 1
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


def build_shareholding_lookup(shareholding_df: pd.DataFrame) -> Dict[str, Dict]:
    """
    Parses the NSE CF-Shareholding-Pattern CSV.
    Exact column names:
      COMPANY | PROMOTER & PROMOTER GROUP (A) | PUBLIC (B) |
      SHARES HELD BY EMPLOYEE TRUSTS (C2) | STATUS | AS ON DATE | REVISION DATE | ACTION
    Returns dict keyed by base ticker.
    """
    lookup: Dict[str, Dict] = {}
    if shareholding_df is None or shareholding_df.empty:
        return lookup

    df = shareholding_df.copy()
    df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in df.columns]

    if SH_COL_COMPANY not in df.columns:
        st.error(
            f"Shareholding CSV missing expected column '{SH_COL_COMPANY}'. "
            f"Found columns: {list(df.columns[:6])}"
        )
        return lookup

    def normalise(s: str) -> str:
        return s.lower().strip()

    company_map: Dict[str, pd.Series] = {}
    for _, row in df.iterrows():
        cname = str(row[SH_COL_COMPANY]).strip()
        company_map[normalise(cname)] = row

    for ticker, company_name in TICKER_TO_COMPANY.items():
        norm_name = normalise(company_name)
        row = company_map.get(norm_name)

        if row is None:
            for ckey, crow in company_map.items():
                if norm_name[:25] in ckey or ckey[:25] in norm_name:
                    row = crow
                    break

        if row is None:
            continue

        def pct_val(col_name: str) -> Optional[float]:
            if col_name not in row.index:
                return None
            v = row[col_name]
            try:
                return float(str(v).replace("%", "").strip())
            except Exception:
                return None

        promoter_pct  = pct_val(SH_COL_PROMOTER)
        public_pct    = pct_val(SH_COL_PUBLIC)
        emp_pct       = pct_val(SH_COL_EMP_TRUST)
        as_on_date    = str(row[SH_COL_AS_ON]).strip()    if SH_COL_AS_ON    in row.index else None
        revision_date = str(row[SH_COL_REVISION]).strip() if SH_COL_REVISION in row.index else None
        action_link   = str(row[SH_COL_ACTION]).strip()   if SH_COL_ACTION   in row.index else None
        sh_status     = str(row[SH_COL_STATUS]).strip()   if SH_COL_STATUS   in row.index else None

        parts     = [p for p in [promoter_pct, public_pct, emp_pct] if p is not None]
        total_own = round(sum(parts), 2) if parts else None

        ownership_valid = (
            promoter_pct is not None
            and public_pct is not None
            and total_own is not None
            and abs(total_own - 100.0) < 5.0
        )

        lookup[ticker] = {
            "PromoterPct_NSE":          promoter_pct,
            "PublicPct_NSE":            public_pct,
            "EmployeeTrustPct_NSE":     emp_pct,
            "OwnershipTotalPct":        total_own,
            "OwnershipDataValid":       ownership_valid,
            "ShareholdingStatus":       "NSE CSV",
            "ShareholdingAsOnDate":     as_on_date,
            "ShareholdingRevisionDate": revision_date,
            "ShareholdingActionLink":   action_link,
            "NSE_SH_Status":            sh_status,
            "HasShareholdingData":      True,
        }

    return lookup


def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()
    df = nse_df.copy()
    required_cols = ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"NSE bhavcopy CSV is missing required column: {col}")
            return pd.DataFrame()
    df = df[df["FinInstrmTp"] == "STK"]
    df = df[df["SctySrs"] == "EQ"]
    if df.empty:
        return pd.DataFrame()
    df = df[["TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol", "TtlTrfVal"]].copy()
    df = df.rename(columns={
        "TckrSymb": "Ticker", "SctySrs": "Series",
        "ClsPric": "Close", "TtlTradgVol": "Volume", "TtlTrfVal": "Turnover"
    })
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


# -----------------------------
# VERDICT LOGIC
# -----------------------------
def compute_screen_verdict(
    l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
    l1_data_missing, l2_data_missing, l3_data_missing,
    l4_data_missing, l5_data_missing,
    conviction, final_pass,
) -> str:
    layers_missing = [l1_data_missing, l2_data_missing, l3_data_missing,
                      l4_data_missing, l5_data_missing]
    layers_pass    = [l1_val, l2_prof, l3_cf, l4_share, l5_forensic]
    testable_count = sum(1 for m in layers_missing if not m)
    if testable_count < 3:
        return VERDICT_FAIL_NODATA
    genuine_failure = any(
        not passed and not missing
        for passed, missing in zip(layers_pass, layers_missing)
    )
    if genuine_failure:
        return VERDICT_FAIL_GENUINE
    if any(layers_missing):
        return VERDICT_PASS_DATAGAP
    return VERDICT_PASS


# -----------------------------
# CORE EVALUATION
# -----------------------------
def evaluate_stock(ticker: str) -> Dict[str, Any]:
    try:
        yf_ticker    = yf.Ticker(ticker)
        base_ticker  = ticker.replace(".NS", "").upper()
        fund_row     = fundamentals_lookup.get(base_ticker)
        sh_data      = shareholding_lookup.get(base_ticker)
        info         = yf_ticker.info

        pe           = safe(info, "trailingPE")
        pb           = safe(info, "priceToBook")
        ev_ebitda    = safe(info, "enterpriseToEbitda")
        roe          = safe(info, "returnOnEquity")
        roa          = safe(info, "returnOnAssets")
        opm          = safe(info, "operatingMargins")
        revg         = safe(info, "revenueGrowth")
        earng        = safe(info, "earningsGrowth")
        fcf          = safe(info, "freeCashflow")
        ocf          = safe(info, "operatingCashflow")
        ni           = safe(info, "netIncomeToCommon")
        de           = safe(info, "debtToEquity")
        insider      = safe(info, "heldPercentInsiders")
        mcap_raw     = safe(info, "marketCap") or 0
        price        = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector       = safe(info, "sector", "N/A")
        ebit         = safe(info, "ebit")
        ta           = safe(info, "totalAssets")
        current_liab = safe(info, "totalCurrentLiabilities")

        mcap_cr = mcap_raw / 1e7 if mcap_raw else None

        roce = None
        if ebit and ta and current_liab is not None:
            cap_employed = ta - current_liab
            if cap_employed > 0:
                roce = ebit / cap_employed

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

        # Overrides from fundamentals_master
        if fund_row is not None:
            for fm_col, var_name in [
                ("ROE_Latest",            "roe"),
                ("ROCE_Latest",           "roce"),
                ("OPM_Latest",            "opm"),
                ("Revenue_CAGR_AllYears", "revg"),
                ("PAT_CAGR_AllYears",     "earng"),
            ]:
                if fm_col in fund_row.index:
                    v = parse_percent_or_float(fund_row[fm_col])
                    if v is not None:
                        if var_name == "roe":   roe   = v
                        if var_name == "roce":  roce  = v
                        if var_name == "opm":   opm   = v
                        if var_name == "revg":  revg  = v
                        if var_name == "earng": earng = v

        # -------------------------------------------------------
        # L4 OWNERSHIP — NSE shareholding CSV (primary)
        # Falls back to yfinance heldPercentInsiders
        # -------------------------------------------------------
        promoter_pct_nse      = None
        public_pct_nse        = None
        emp_trust_pct_nse     = None
        ownership_total_pct   = None
        ownership_data_valid  = False
        has_shareholding_data = False
        sh_as_on_date         = None
        sh_revision_date      = None
        sh_action_link        = None
        shareholding_status   = "Not available"

        if sh_data is not None:
            promoter_pct_nse      = sh_data.get("PromoterPct_NSE")
            public_pct_nse        = sh_data.get("PublicPct_NSE")
            emp_trust_pct_nse     = sh_data.get("EmployeeTrustPct_NSE")
            ownership_total_pct   = sh_data.get("OwnershipTotalPct")
            ownership_data_valid  = sh_data.get("OwnershipDataValid", False)
            has_shareholding_data = sh_data.get("HasShareholdingData", False)
            sh_as_on_date         = sh_data.get("ShareholdingAsOnDate")
            sh_revision_date      = sh_data.get("ShareholdingRevisionDate")
            sh_action_link        = sh_data.get("ShareholdingActionLink")
            shareholding_status   = "NSE CSV" if has_shareholding_data else "Not available"

        if promoter_pct_nse is not None:
            l4_share        = ownership_data_valid and (promoter_pct_nse / 100.0) >= CONFIG["promoter_min"]
            l4_data_missing = not has_shareholding_data
        else:
            l4_share        = insider is not None and insider > CONFIG["insider_min"]
            l4_data_missing = insider is None
            shareholding_status = "yfinance (fallback)" if insider is not None else "Not available"

        # L1 Valuation
        l1_checks = [
            pe        is not None and pe        < CONFIG["pe_max"],
            peg       is not None and peg       < CONFIG["peg_max"],
            ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
            pb        is not None and pb        < CONFIG["pb_max"],
            mcap_cr   is not None and CONFIG["mcap_min_cr"] <= mcap_cr <= CONFIG["mcap_max_cr"],
        ]
        l1_available    = [pe is not None, peg is not None, ev_ebitda is not None,
                           pb is not None, mcap_cr is not None]
        l1_val          = sum(l1_checks) >= 3
        l1_data_missing = sum(l1_available) < 3

        # L2 Profitability
        l2_checks = [
            roce  is not None and roce  > CONFIG["roce_min"],
            roe   is not None and roe   > CONFIG["roe_min"],
            roa   is not None and roa   > CONFIG["roa_min"],
            opm   is not None and opm   > CONFIG["opm_min"],
            revg  is not None and revg  > CONFIG["rev_growth_min"],
            earng is not None and earng > CONFIG["earn_growth_min"],
        ]
        l2_available    = [roce is not None, roe is not None, roa is not None,
                           opm is not None, revg is not None, earng is not None]
        l2_prof         = sum(l2_checks) >= 4
        l2_data_missing = sum(l2_available) < 4

        # L3 Cash flow
        l3_checks = [
            ocf_pat   is not None and ocf_pat   > CONFIG["ocf_pat_min"],
            fcf_yield is not None and fcf_yield > CONFIG["fcf_yield_min"],
            de_ratio  is not None and de_ratio  < CONFIG["de_max"],
        ]
        l3_available    = [ocf_pat is not None, fcf_yield is not None, de_ratio is not None]
        l3_cf           = sum(l3_checks) >= 2
        l3_data_missing = sum(l3_available) < 2

        # L5 Forensic quality
        l5_fields_present = sum([
            safe(info, "netIncomeToCommon")   is not None,
            safe(info, "operatingCashflow")   is not None,
            safe(info, "returnOnAssets")      is not None,
            safe(info, "longTermDebt")        is not None,
            safe(info, "totalAssets")         is not None,
            safe(info, "currentRatio")        is not None,
            safe(info, "grossMargins")        is not None,
        ])
        l5_forensic     = quality_raw >= CONFIG["quality_min_raw"]
        l5_data_missing = l5_fields_present < 4

        conviction = sum([l1_val, l2_prof, l3_cf, l4_share, l5_forensic])
        final_pass = bool(l2_prof and l5_forensic and conviction >= 4)

        verdict = compute_screen_verdict(
            l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
            l1_data_missing, l2_data_missing, l3_data_missing,
            l4_data_missing, l5_data_missing,
            conviction, final_pass,
        )

        ws = 0
        ws += 5 if pe        is not None and pe        < 20   else 0
        ws += 5 if peg       is not None and peg       < 1    else 0
        ws += 5 if ev_ebitda is not None and ev_ebitda < 12   else 0
        ws += 3 if pb        is not None and pb        < 3    else 0
        ws += 2 if mcap_cr   is not None and 200 <= mcap_cr <= 5000 else 0
        ws += 8 if roce      is not None and roce      > 0.20 else 0
        ws += 6 if roe       is not None and roe       > 0.18 else 0
        ws += 4 if roa       is not None and roa       > 0.10 else 0
        ws += 4 if opm       is not None and opm       > 0.15 else 0
        ws += 4 if revg      is not None and revg      > 0.15 else 0
        ws += 4 if earng     is not None and earng     > 0.20 else 0
        ws += 8 if ocf_pat   is not None and ocf_pat   > 0.8  else 0
        ws += 6 if fcf_yield is not None and fcf_yield > 0.03 else 0
        ws += 6 if de_ratio  is not None and de_ratio  < 0.5  else 0
        ws += 5 if l4_share else 0
        qp  = round(10 * quality_raw / 7) if quality_raw is not None else 0
        ws += min(qp, 10)

        ownership_anomaly = None
        if promoter_pct_nse is not None and ownership_data_valid:
            if promoter_pct_nse < 25.0:
                ownership_anomaly = f"Low promoter holding: {promoter_pct_nse:.1f}%"
            elif public_pct_nse is not None and public_pct_nse > 70.0:
                ownership_anomaly = f"High public float: {public_pct_nse:.1f}%"

        return {
            "Ticker":                   base_ticker,
            "Sector":                   sector,
            "ScreenVerdict":            verdict,
            "Price":                    price,
            "MCap_Cr":                  round(mcap_cr, 1) if mcap_cr else None,
            "PE":                       round(pe, 2)      if pe else None,
            "PB":                       round(pb, 2)      if pb else None,
            "PEG":                      round(peg, 2)     if peg else None,
            "ROCE_pct":                 round(roce * 100, 1)      if roce      is not None else None,
            "ROE_pct":                  round(roe * 100, 1)       if roe       is not None else None,
            "ROA_pct":                  round(roa * 100, 1)       if roa       is not None else None,
            "OPM_pct":                  round(opm * 100, 1)       if opm       is not None else None,
            "RevGrowth_pct":            round(revg * 100, 1)      if revg      is not None else None,
            "EarnGrowth_pct":           round(earng * 100, 1)     if earng     is not None else None,
            "OCF_PAT":                  round(ocf_pat, 2)         if ocf_pat   is not None else None,
            "FCFYield_pct":             round(fcf_yield * 100, 2) if fcf_yield is not None else None,
            "YahooInsider_pct":         round(insider * 100, 2)   if insider   is not None else None,
            "PromoterPct_NSE":          promoter_pct_nse,
            "PublicPct_NSE":            public_pct_nse,
            "EmployeeTrustPct_NSE":     emp_trust_pct_nse,
            "OwnershipTotalPct":        ownership_total_pct,
            "OwnershipDataValid":       ownership_data_valid,
            "ShareholdingStatus":       shareholding_status,
            "ShareholdingAsOnDate":     sh_as_on_date,
            "ShareholdingRevisionDate": sh_revision_date,
            "OwnershipAnomaly":         ownership_anomaly,
            "QualityScore_raw":         quality_raw,
            "L1_Val":                   l1_val,
            "L2_Prof":                  l2_prof,
            "L3_CF":                    l3_cf,
            "L4_Share":                 l4_share,
            "L5_Forensic":              l5_forensic,
            "L1_DataMissing":           l1_data_missing,
            "L2_DataMissing":           l2_data_missing,
            "L3_DataMissing":           l3_data_missing,
            "L4_DataMissing":           l4_data_missing,
            "L5_DataMissing":           l5_data_missing,
            "Conviction":               conviction,
            "WeightedScore":            ws,
            "Pass":                     final_pass,
            "HasFundamentals":          fund_row is not None,
            "HasShareholdingData":      has_shareholding_data,
            "ShareholdingActionLink":   sh_action_link,
            "Error":                    None,
        }

    except Exception as e:
        base_ticker = ticker.replace(".NS", "")
        return {
            "Ticker": base_ticker, "Sector": None,
            "ScreenVerdict": VERDICT_FAIL_NODATA,
            "Price": None, "MCap_Cr": None,
            "PE": None, "PB": None, "PEG": None,
            "ROCE_pct": None, "ROE_pct": None, "ROA_pct": None, "OPM_pct": None,
            "RevGrowth_pct": None, "EarnGrowth_pct": None,
            "OCF_PAT": None, "FCFYield_pct": None,
            "YahooInsider_pct": None,
            "PromoterPct_NSE": None, "PublicPct_NSE": None,
            "EmployeeTrustPct_NSE": None, "OwnershipTotalPct": None,
            "OwnershipDataValid": False, "ShareholdingStatus": "Error",
            "ShareholdingAsOnDate": None, "ShareholdingRevisionDate": None,
            "OwnershipAnomaly": None,
            "QualityScore_raw": None,
            "L1_Val": False, "L2_Prof": False, "L3_CF": False,
            "L4_Share": False, "L5_Forensic": False,
            "L1_DataMissing": True, "L2_DataMissing": True, "L3_DataMissing": True,
            "L4_DataMissing": True, "L5_DataMissing": True,
            "Conviction": 0, "WeightedScore": 0, "Pass": False,
            "HasFundamentals": False, "HasShareholdingData": False,
            "ShareholdingActionLink": None, "Error": str(e),
        }


# -----------------------------
# SIDEBAR — distinct key= for every file_uploader
# -----------------------------
st.sidebar.header("Controls")
min_score    = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass    = st.sidebar.checkbox("Show only final pass names", value=True)
show_datagap = st.sidebar.checkbox(
    "Also show PASS (Data gaps present)", value=True,
    help="Include stocks that pass all testable layers but have some missing data fields."
)
max_stocks = st.sidebar.number_input(
    "Max stocks to screen (top by NSE turnover)",
    min_value=10, max_value=500, value=50, step=10,
)

screen_mode = st.sidebar.radio(
    "Screen mode",
    ["Mid/Small cap (₹200–5000 Cr)", "All cap"],
    index=0,
    help="Use Mid/Small cap for original 100X logic. All cap keeps the full turnover-based universe.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE bhavcopy (price universe)")
uploaded_nse_file = st.sidebar.file_uploader(
    "Upload NSE EOD CSV (weekly bhavcopy)",
    type=["csv"],
    key="nse_bhavcopy_upload",
    help="Download the equity bhavcopy from NSE India on Friday night, then upload here.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE shareholding (L4 ownership)")
uploaded_sh_file = st.sidebar.file_uploader(
    "Upload CF-Shareholding-Pattern CSV",
    type=["csv"],
    key="nse_shareholding_upload",
    help="Download from NSE India → Corporate Filings → Shareholding Pattern.",
)

pause_between_calls = st.sidebar.slider(
    "Pause between API calls (seconds)",
    min_value=0.0, max_value=1.0, value=0.2, step=0.1,
)
st.sidebar.write(f"Default universe: {len(DEFAULT_UNIVERSE)} tickers")
st.sidebar.write(f"Ticker→Company map: {len(TICKER_TO_COMPANY)} entries")

with st.expander("ScreenVerdict legend", expanded=False):
    st.markdown("""
| Verdict | Meaning |
|---|---|
| **PASS** | Passes all 5 layers; no data gaps. |
| **PASS (Data gaps present)** | Passes every testable layer; some layers untestable. Treat as a qualified pass worth deeper manual review. |
| **FAIL (Genuine)** | Fails at least one layer where real data *is* available. |
| **FAIL (Insufficient data)** | Fewer than 3 layers could be tested. No reliable conclusion possible. |
""")

with st.expander("L4 Ownership source priority", expanded=False):
    st.markdown("""
**Priority:**
1. **NSE shareholding CSV** — promoter % ≥ 40%. Most reliable. Matched via `TICKER_TO_COMPANY` map.
2. **yfinance `heldPercentInsiders`** — fallback when CSV not uploaded or ticker not in map.

The **ShareholdingStatus** column shows which source was used per stock.
To add tickers, edit `TICKER_TO_COMPANY` at the top of this script.
""")

# Fundamentals preview
st.subheader("Fundamentals master")
with st.expander("Show fundamentals_master.csv", expanded=False):
    fundamentals_df = load_fundamentals_master()
    if fundamentals_df.empty:
        st.info("fundamentals_master.csv not found or empty.")
    else:
        st.write(f"Loaded {len(fundamentals_df)} stock(s)")
        st.dataframe(fundamentals_df, use_container_width=True)

# Stock master preview
st.subheader("Stock master (sector & subsector)")
with st.expander("Show stock_master.csv", expanded=False):
    stock_master_df = load_stock_master()
    if stock_master_df.empty:
        st.info("stock_master.csv not found or empty.")
    else:
        st.write(f"Loaded {len(stock_master_df)} stock(s)")
        st.dataframe(stock_master_df, use_container_width=True)

# Shareholding CSV preview + match check
st.subheader("NSE shareholding data")
with st.expander("Show shareholding CSV preview + match check", expanded=False):
    if uploaded_sh_file is None:
        st.info(
            "No shareholding CSV uploaded. L4 will fall back to yfinance. "
            "Download from NSE → Corporate Filings → Shareholding Pattern "
            "→ CF-Shareholding-Pattern-equities-{date}.csv"
        )
    else:
        try:
            uploaded_sh_file.seek(0)
            sh_preview_df = pd.read_csv(uploaded_sh_file)
            sh_preview_df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in sh_preview_df.columns]
            st.write(f"Loaded {len(sh_preview_df)} rows, {len(sh_preview_df.columns)} columns.")
            st.caption(f"Columns detected: {list(sh_preview_df.columns)}")
            st.dataframe(sh_preview_df.head(5), use_container_width=True)

            test_lookup = build_shareholding_lookup(sh_preview_df)
            matched   = sorted([t for t in TICKER_TO_COMPANY if t in test_lookup])
            unmatched = sorted([t for t in TICKER_TO_COMPANY if t not in test_lookup])
            if matched:
                st.success(f"✅ Matched {len(matched)} ticker(s): {matched}")
            if unmatched:
                st.warning(f"⚠️ Unmatched {len(unmatched)} ticker(s): {unmatched}")
            if matched:
                preview_rows = []
                for t in matched:
                    d = test_lookup[t]
                    preview_rows.append({
                        "Ticker":      t,
                        "Company":     TICKER_TO_COMPANY[t],
                        "PromoterPct": d.get("PromoterPct_NSE"),
                        "PublicPct":   d.get("PublicPct_NSE"),
                        "AsOnDate":    d.get("ShareholdingAsOnDate"),
                        "DataValid":   d.get("OwnershipDataValid"),
                    })
                st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)
        except Exception as e:
            st.error(f"Error reading shareholding CSV: {e}")

# Bhavcopy preview
st.subheader("NSE bhavcopy (price universe)")
with st.expander("Show uploaded NSE bhavcopy preview", expanded=False):
    if uploaded_nse_file is None:
        st.info("No bhavcopy uploaded. App will screen DEFAULT_UNIVERSE of 10 tickers.")
    else:
        try:
            uploaded_nse_file.seek(0)
            nse_prices_df = pd.read_csv(uploaded_nse_file)
            st.write(f"Loaded {len(nse_prices_df)} rows.")
            st.dataframe(nse_prices_df.head(10), use_container_width=True)
            equity_universe_df = build_nse_equity_universe(nse_prices_df)
            if equity_universe_df is not None and not equity_universe_df.empty:
                st.write(f"Equity universe: {len(equity_universe_df)} stocks. Top 50 by turnover:")
                st.dataframe(equity_universe_df.head(50), use_container_width=True)
        except Exception as e:
            st.error(f"Error reading bhavcopy CSV: {e}")

# -----------------------------
# MAIN ACTION
# -----------------------------
if st.button("Run live screen"):

    # Build shareholding lookup
    if uploaded_sh_file is not None:
        try:
            uploaded_sh_file.seek(0)
            sh_raw_df = pd.read_csv(uploaded_sh_file)
            sh_raw_df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in sh_raw_df.columns]
            shareholding_lookup = build_shareholding_lookup(sh_raw_df)
            st.info(
                f"Shareholding lookup built: {len(shareholding_lookup)} ticker(s) matched "
                f"→ {list(shareholding_lookup.keys())}"
            )
        except Exception as e:
            st.warning(f"Could not build shareholding lookup: {e}")
            shareholding_lookup = {}
    else:
        shareholding_lookup = {}
        st.warning("No shareholding CSV uploaded. L4 will use yfinance insiderHoldingsPercent (less reliable for Indian stocks).")

    # Build equity universe
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
        base_universe     = equity_universe_df_local.head(int(max_stocks))
        universe_tickers  = base_universe["Ticker"].astype(str).str.upper().tolist()
        tickers_to_screen = [f"{t}.NS" for t in universe_tickers]
        st.info(f"Using NSE bhavcopy universe: screening top {len(tickers_to_screen)} stock(s) by turnover.")
    else:
        tickers_to_screen = DEFAULT_UNIVERSE
        st.warning("No NSE bhavcopy uploaded; falling back to DEFAULT_UNIVERSE list.")

    fundamentals_master_df = load_fundamentals_master()
    stock_master_df        = load_stock_master()
    rebuild_fundamentals_lookup(fundamentals_master_df)

    st.write(f"Found {len(tickers_to_screen)} stocks to screen")

    rows: List[Dict[str, Any]] = []
    progress_bar  = st.progress(0)
    status_text   = st.empty()
    total_tickers = len(tickers_to_screen)

    with st.spinner("Fetching live data from Yahoo Finance..."):
        for i, ticker in enumerate(tickers_to_screen):
            status_text.text(f"Screening {ticker} ({i+1}/{total_tickers})...")
            row = evaluate_stock(ticker)
            if row:
                rows.append(row)
            progress_bar.progress((i + 1) / total_tickers)
            time.sleep(pause_between_calls)

    status_text.empty()
    progress_bar.empty()

    df = pd.DataFrame(rows)

    # Merge sector/subsector from stock_master
    if not df.empty and stock_master_df is not None and not stock_master_df.empty:
        merge_cols = [c for c in ["Ticker", "Sector", "SubSector"] if c in stock_master_df.columns]
        if len(merge_cols) > 1:
            df = df.merge(
                stock_master_df[merge_cols],
                on="Ticker", how="left", suffixes=("", "_stock"),
            )
            if "Sector_stock" in df.columns:
                df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
                df.drop(columns=["Sector_stock"], inplace=True)
            if "SubSector_stock" in df.columns:
                df.rename(columns={"SubSector_stock": "SubSector"}, inplace=True)

    # Merge fundamentals columns
    if not df.empty and fundamentals_master_df is not None and not fundamentals_master_df.empty:
        fund_merge_cols = [
            "Ticker", "Latest_Year",
            "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
            "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
            "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
            "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag",
            "Reg_Risk_Flag", "Gov_Risk_Flag",
        ]
        fund_merge_cols = [c for c in fund_merge_cols if c in fundamentals_master_df.columns]
        df = df.merge(
            fundamentals_master_df[fund_merge_cols],
            on="Ticker", how="left", suffixes=("", "_fund"),
        )

    # Apply filters
    screened_count = len(df)

    if screen_mode == "Mid/Small cap (₹200–5000 Cr)":
        pre_cap_count = len(df)
        df = df[
            df["MCap_Cr"].notna()
            & (df["MCap_Cr"] >= CONFIG["mcap_min_cr"])
            & (df["MCap_Cr"] <= CONFIG["mcap_max_cr"])
        ].copy()
        st.info(
            f"Screen mode applied: Mid/Small cap "
            f"({CONFIG['mcap_min_cr']:.0f}–{CONFIG['mcap_max_cr']:.0f} Cr) — "
            f"{len(df)} of {pre_cap_count} screened stocks remain."
        )
    else:
        st.info("Screen mode applied: All cap — no market-cap filter.")

    if only_pass:
        if show_datagap:
            df = df[df["ScreenVerdict"].isin([VERDICT_PASS, VERDICT_PASS_DATAGAP])].copy()
        else:
            df = df[df["ScreenVerdict"] == VERDICT_PASS].copy()

    if min_score > 0:
        df = df[df["Conviction"] >= min_score].copy()

    # Sort
    verdict_order = {
        VERDICT_PASS: 0, VERDICT_PASS_DATAGAP: 1,
        VERDICT_FAIL_GENUINE: 2, VERDICT_FAIL_NODATA: 3,
    }
    df["_vsort"] = df["ScreenVerdict"].map(verdict_order).fillna(9).astype(int)
    df = df.sort_values(["_vsort", "WeightedScore", "Conviction"],
                        ascending=[True, False, False])
    df.drop(columns=["_vsort"], inplace=True)

    # Column order
    preferred_order = [
        "Ticker", "Sector", "SubSector", "ScreenVerdict", "Price", "MCap_Cr",
        "PE", "PB", "PEG", "ROCE_pct", "ROE_pct", "ROA_pct", "OPM_pct",
        "RevGrowth_pct", "EarnGrowth_pct", "OCF_PAT", "FCFYield_pct",
        "YahooInsider_pct",
        "PromoterPct_NSE", "PublicPct_NSE", "EmployeeTrustPct_NSE",
        "OwnershipTotalPct", "OwnershipDataValid",
        "ShareholdingStatus", "ShareholdingAsOnDate", "ShareholdingRevisionDate",
        "OwnershipAnomaly",
        "QualityScore_raw",
        "L1_Val", "L2_Prof", "L3_CF", "L4_Share", "L5_Forensic",
        "L1_DataMissing", "L2_DataMissing", "L3_DataMissing",
        "L4_DataMissing", "L5_DataMissing",
        "Conviction", "WeightedScore", "Pass",
        "HasFundamentals", "HasShareholdingData",
        "Latest_Year", "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
        "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
        "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
        "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag", "Reg_Risk_Flag", "Gov_Risk_Flag",
        "Error", "ShareholdingActionLink",
    ]
    existing_cols  = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in existing_cols]
    df = df[existing_cols + remaining_cols]

    total     = len(df)
    n_pass    = (df["ScreenVerdict"] == VERDICT_PASS).sum()
    n_datagap = (df["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum()
    n_genuine = (df["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum()
    n_nodata  = (df["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum()

    st.success(f"Screen complete — {screened_count} screened, {total} stock(s) shown")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("PASS", n_pass)
    col2.metric("PASS (Data gaps)", n_datagap)
    col3.metric("FAIL (Genuine)", n_genuine)
    col4.metric("FAIL (No data)", n_nodata)

    if not df.empty:
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download CSV",
            data=df.to_csv(index=False),
            file_name="100x_screener_v4_results.csv",
            mime="text/csv",
        )
    else:
        st.info(
            "No stocks passed the current filters. "
            "Try lowering the conviction score threshold or unchecking 'Show only final pass names'."
        )

    if "OwnershipAnomaly" in df.columns and not df.empty:
        anomalies = df[df["OwnershipAnomaly"].notna()][["Ticker", "PromoterPct_NSE", "OwnershipAnomaly"]]
        if anomalies.empty:
            st.info("No ownership anomalies in screened names.")
        else:
            st.warning("Ownership anomalies detected:")
            st.dataframe(anomalies, use_container_width=True)

else:
    st.info("Click **Run live screen** to start.")
