import time
from typing import Dict, Any, List, Optional

import pandas as pd
import streamlit as st
import yfinance as yf

# -----------------------------
# PAGE CONFIG
# -----------------------------
st.set_page_config(page_title="100X Screener V1 - Indian Equities", layout="wide")

# -----------------------------
# TICKER → NSE COMPANY NAME MAP
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

SH_COL_COMPANY   = "COMPANY"
SH_COL_PROMOTER  = "PROMOTER & PROMOTER GROUP (A)"
SH_COL_PUBLIC    = "PUBLIC (B)"
SH_COL_EMP_TRUST = "SHARES HELD BY EMPLOYEE TRUSTS (C2)"
SH_COL_STATUS    = "STATUS"
SH_COL_AS_ON     = "AS ON DATE"
SH_COL_REVISION  = "REVISION DATE"
SH_COL_ACTION    = "ACTION"

fundamentals_lookup: Dict[str, Any] = {}
shareholding_lookup: Dict[str, Dict] = {}

st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "V1 = Single-page Streamlit app using free Yahoo Finance data via yfinance. "
    "Acts as a narrowing engine, not a buy/sell signal."
)
st.sidebar.caption(f"yfinance version: {yf.__version__}")

with st.expander("What this V1 actually does / does NOT do", expanded=False):
    st.markdown("""
- **Implements (V1 reality):**
  - Screens NSE stocks using `yfinance.Ticker.info` plus your curated `fundamentals_master.csv`.
  - L4 Ownership uses NSE official shareholding CSV (promoter ≥ 40%) when uploaded, else falls back to yfinance insider holdings.
  - Computes L1–L5, Conviction, WeightedScore, and a **ScreenVerdict** that distinguishes genuine failures from data-gap failures.
  - Displays results in a table with CSV download.
  - Adds a failure-reason summary for failed names.

- **Does *not* implement (future versions only):**
  - Watchlist persistence or score history.
  - Alerts.
  - Backtests.
  - Pledge-level shareholding or detailed promoter analytics.
  - Alternative data providers beyond yfinance.
""")

st.markdown(
    "<sub>Indian cap classification is officially rank-based under SEBI/AMFI. "
    "This app uses practical rupee market-cap proxy bands for screening convenience.</sub>",
    unsafe_allow_html=True,
)

DEFAULT_UNIVERSE: List[str] = [
    "LLOYDSME.NS", "POLYCAB.NS", "DEEPAKNTR.NS", "CGPOWER.NS", "TANLA.NS",
    "KPITTECH.NS", "CDSL.NS", "CAMS.NS", "IRCTC.NS", "OLECTRA.NS",
]

CONFIG: Dict[str, Any] = {
    "pe_max": 20.0,
    "peg_max": 1.0,
    "ev_ebitda_max": 12.0,
    "pb_max": 3.0,
    "roce_min": 0.20,
    "roe_min": 0.18,
    "roa_min": 0.10,
    "opm_min": 0.15,
    "rev_growth_min": 0.15,
    "earn_growth_min": 0.20,
    "ocf_pat_min": 0.80,
    "fcf_yield_min": 0.03,
    "de_max": 0.50,
    "promoter_min": 0.40,
    "insider_min": 0.40,
    "quality_min_raw": 5,

    # Practical non-overlapping cap bands
    "nano_small_max_cr": 5000.0,
    "mid_min_cr": 5000.0,
    "mid_max_cr": 20000.0,
}

VERDICT_PASS         = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA  = "FAIL (Insufficient data)"


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
    if fundamentals_master_df is None or fundamentals_master_df.empty or "Ticker" not in fundamentals_master_df.columns:
        return
    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    fundamentals_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def build_shareholding_lookup(shareholding_df: pd.DataFrame) -> Dict[str, Dict]:
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
        as_on_date    = str(row[SH_COL_AS_ON]).strip() if SH_COL_AS_ON in row.index else None
        revision_date = str(row[SH_COL_REVISION]).strip() if SH_COL_REVISION in row.index else None
        action_link   = str(row[SH_COL_ACTION]).strip() if SH_COL_ACTION in row.index else None
        sh_status     = str(row[SH_COL_STATUS]).strip() if SH_COL_STATUS in row.index else None

        parts = [p for p in [promoter_pct, public_pct, emp_pct] if p is not None]
        total_own = round(sum(parts), 2) if parts else None

        ownership_valid = (
            promoter_pct is not None
            and public_pct is not None
            and total_own is not None
            and abs(total_own - 100.0) < 5.0
        )

        lookup[ticker] = {
            "PromoterPct_NSE": promoter_pct,
            "PublicPct_NSE": public_pct,
            "EmployeeTrustPct_NSE": emp_pct,
            "OwnershipTotalPct": total_own,
            "OwnershipDataValid": ownership_valid,
            "ShareholdingStatus": "NSE CSV",
            "ShareholdingAsOnDate": as_on_date,
            "ShareholdingRevisionDate": revision_date,
            "ShareholdingActionLink": action_link,
            "NSE_SH_Status": sh_status,
            "HasShareholdingData": True,
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
        "TckrSymb": "Ticker",
        "SctySrs": "Series",
        "ClsPric": "Close",
        "TtlTradgVol": "Volume",
        "TtlTrfVal": "Turnover",
    })
    df["Ticker"] = df["Ticker"].astype(str).str.upper()
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def add_mcap_hint_from_masters(
    equity_df: pd.DataFrame,
    stock_master_df: pd.DataFrame,
    fundamentals_master_df: pd.DataFrame,
) -> pd.DataFrame:
    if equity_df is None or equity_df.empty:
        return pd.DataFrame()

    df = equity_df.copy()
    df["MCapHint_Cr"] = pd.NA

    for master_df in [stock_master_df, fundamentals_master_df]:
        if master_df is None or master_df.empty or "Ticker" not in master_df.columns:
            continue

        tmp = master_df.copy()
        tmp["Ticker"] = tmp["Ticker"].astype(str).str.upper()

        hint_col = None
        for c in tmp.columns:
            cl = c.lower().strip()
            if cl in ["mcap_cr", "marketcap_cr", "market_cap_cr", "mcap", "marketcap", "market_cap"]:
                hint_col = c
                break

        if hint_col is not None:
            tmp["MCapHint_Cr_tmp"] = pd.to_numeric(tmp[hint_col], errors="coerce")
            df = df.merge(tmp[["Ticker", "MCapHint_Cr_tmp"]], on="Ticker", how="left")
            df["MCapHint_Cr"] = df["MCapHint_Cr"].fillna(df["MCapHint_Cr_tmp"])
            df.drop(columns=["MCapHint_Cr_tmp"], inplace=True)

    return df


def in_selected_cap_mode(mcap_cr: Optional[float], screen_mode: str) -> bool:
    if mcap_cr is None:
        return False
    if screen_mode == "Nano/Small cap":
        return mcap_cr < CONFIG["nano_small_max_cr"]
    if screen_mode == "Mid cap":
        return CONFIG["mid_min_cr"] <= mcap_cr < CONFIG["mid_max_cr"]
    return True


def compute_screen_verdict(
    l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
    l1_data_missing, l2_data_missing, l3_data_missing,
    l4_data_missing, l5_data_missing,
    conviction, final_pass,
) -> str:
    layers_missing = [l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing]
    layers_pass = [l1_val, l2_prof, l3_cf, l4_share, l5_forensic]
    testable_count = sum(1 for m in layers_missing if not m)
    if testable_count < 3:
        return VERDICT_FAIL_NODATA
    genuine_failure = any(not passed and not missing for passed, missing in zip(layers_pass, layers_missing))
    if genuine_failure:
        return VERDICT_FAIL_GENUINE
    if any(layers_missing):
        return VERDICT_PASS_DATAGAP
    return VERDICT_PASS


def evaluate_stock(ticker: str, screen_mode: str) -> Dict[str, Any]:
    try:
        yf_ticker = yf.Ticker(ticker)
        base_ticker = ticker.replace(".NS", "").upper()
        fund_row = fundamentals_lookup.get(base_ticker)
        sh_data = shareholding_lookup.get(base_ticker)
        info = yf_ticker.info

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

        if fund_row is not None:
            for fm_col, var_name in [
                ("ROE_Latest", "roe"),
                ("ROCE_Latest", "roce"),
                ("OPM_Latest", "opm"),
                ("Revenue_CAGR_AllYears", "revg"),
                ("PAT_CAGR_AllYears", "earng"),
            ]:
                if fm_col in fund_row.index:
                    v = parse_percent_or_float(fund_row[fm_col])
                    if v is not None:
                        if var_name == "roe":
                            roe = v
                        elif var_name == "roce":
                            roce = v
                        elif var_name == "opm":
                            opm = v
                        elif var_name == "revg":
                            revg = v
                        elif var_name == "earng":
                            earng = v

        promoter_pct_nse = None
        public_pct_nse = None
        emp_trust_pct_nse = None
        ownership_total_pct = None
        ownership_data_valid = False
        has_shareholding_data = False
        sh_as_on_date = None
        sh_revision_date = None
        sh_action_link = None
        shareholding_status = "Not available"

        if sh_data is not None:
            promoter_pct_nse = sh_data.get("PromoterPct_NSE")
            public_pct_nse = sh_data.get("PublicPct_NSE")
            emp_trust_pct_nse = sh_data.get("EmployeeTrustPct_NSE")
            ownership_total_pct = sh_data.get("OwnershipTotalPct")
            ownership_data_valid = sh_data.get("OwnershipDataValid", False)
            has_shareholding_data = sh_data.get("HasShareholdingData", False)
            sh_as_on_date = sh_data.get("ShareholdingAsOnDate")
            sh_revision_date = sh_data.get("ShareholdingRevisionDate")
            sh_action_link = sh_data.get("ShareholdingActionLink")
            shareholding_status = "NSE CSV" if has_shareholding_data else "Not available"

        if promoter_pct_nse is not None:
            l4_share = ownership_data_valid and (promoter_pct_nse / 100.0) >= CONFIG["promoter_min"]
            l4_data_missing = not has_shareholding_data
        else:
            l4_share = insider is not None and insider > CONFIG["insider_min"]
            l4_data_missing = insider is None
            shareholding_status = "yfinance (fallback)" if insider is not None else "Not available"

        cap_check = in_selected_cap_mode(mcap_cr, screen_mode)

        l1_checks = [
            pe is not None and pe < CONFIG["pe_max"],
            peg is not None and peg < CONFIG["peg_max"],
            ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
            pb is not None and pb < CONFIG["pb_max"],
            mcap_cr is not None and cap_check,
        ]
        l1_available = [pe is not None, peg is not None, ev_ebitda is not None, pb is not None, mcap_cr is not None]
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
        l2_available = [roce is not None, roe is not None, roa is not None, opm is not None, revg is not None, earng is not None]
        l2_prof = sum(l2_checks) >= 4
        l2_data_missing = sum(l2_available) < 4

        l3_checks = [
            ocf_pat is not None and ocf_pat > CONFIG["ocf_pat_min"],
            fcf_yield is not None and fcf_yield > CONFIG["fcf_yield_min"],
            de_ratio is not None and de_ratio < CONFIG["de_max"],
        ]
        l3_available = [ocf_pat is not None, fcf_yield is not None, de_ratio is not None]
        l3_cf = sum(l3_checks) >= 2
        l3_data_missing = sum(l3_available) < 2

        l5_fields_present = sum([
            safe(info, "netIncomeToCommon") is not None,
            safe(info, "operatingCashflow") is not None,
            safe(info, "returnOnAssets") is not None,
            safe(info, "longTermDebt") is not None,
            safe(info, "totalAssets") is not None,
            safe(info, "currentRatio") is not None,
            safe(info, "grossMargins") is not None,
        ])
        l5_forensic = quality_raw >= CONFIG["quality_min_raw"]
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
        ws += 5 if pe is not None and pe < 20 else 0
        ws += 5 if peg is not None and peg < 1 else 0
        ws += 5 if ev_ebitda is not None and ev_ebitda < 12 else 0
        ws += 3 if pb is not None and pb < 3 else 0
        ws += 2 if mcap_cr is not None and cap_check else 0
        ws += 8 if roce is not None and roce > 0.20 else 0
        ws += 6 if roe is not None and roe > 0.18 else 0
        ws += 4 if roa is not None and roa > 0.10 else 0
        ws += 4 if opm is not None and opm > 0.15 else 0
        ws += 4 if revg is not None and revg > 0.15 else 0
        ws += 4 if earng is not None and earng > 0.20 else 0
        ws += 8 if ocf_pat is not None and ocf_pat > 0.8 else 0
        ws += 6 if fcf_yield is not None and fcf_yield > 0.03 else 0
        ws += 6 if de_ratio is not None and de_ratio < 0.5 else 0
        ws += 5 if l4_share else 0
        qp = round(10 * quality_raw / 7) if quality_raw is not None else 0
        ws += min(qp, 10)

        ownership_anomaly = None
        if promoter_pct_nse is not None and ownership_data_valid:
            if promoter_pct_nse < 25.0:
                ownership_anomaly = f"Low promoter holding: {promoter_pct_nse:.1f}%"
            elif public_pct_nse is not None and public_pct_nse > 70.0:
                ownership_anomaly = f"High public float: {public_pct_nse:.1f}%"

        fail_reasons = []
        if not l1_val and not l1_data_missing:
            fail_reasons.append("L1 Valuation")
        if not l2_prof and not l2_data_missing:
            fail_reasons.append("L2 Profitability")
        if not l3_cf and not l3_data_missing:
            fail_reasons.append("L3 Cash Flow")
        if not l4_share and not l4_data_missing:
            fail_reasons.append("L4 Shareholding")
        if not l5_forensic and not l5_data_missing:
            fail_reasons.append("L5 Forensic")

        data_gap_reasons = []
        if l1_data_missing:
            data_gap_reasons.append("L1")
        if l2_data_missing:
            data_gap_reasons.append("L2")
        if l3_data_missing:
            data_gap_reasons.append("L3")
        if l4_data_missing:
            data_gap_reasons.append("L4")
        if l5_data_missing:
            data_gap_reasons.append("L5")

        return {
            "Ticker": base_ticker,
            "Sector": sector,
            "ScreenVerdict": verdict,
            "FailReasons": "; ".join(fail_reasons) if fail_reasons else None,
            "DataGapReasons": "; ".join(data_gap_reasons) if data_gap_reasons else None,
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
            "YahooInsider_pct": round(insider * 100, 2) if insider is not None else None,
            "PromoterPct_NSE": promoter_pct_nse,
            "PublicPct_NSE": public_pct_nse,
            "EmployeeTrustPct_NSE": emp_trust_pct_nse,
            "OwnershipTotalPct": ownership_total_pct,
            "OwnershipDataValid": ownership_data_valid,
            "ShareholdingStatus": shareholding_status,
            "ShareholdingAsOnDate": sh_as_on_date,
            "ShareholdingRevisionDate": sh_revision_date,
            "OwnershipAnomaly": ownership_anomaly,
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
            "WeightedScore": ws,
            "Pass": final_pass,
            "HasFundamentals": fund_row is not None,
            "HasShareholdingData": has_shareholding_data,
            "ShareholdingActionLink": sh_action_link,
            "Error": None,
        }

    except Exception as e:
        base_ticker = ticker.replace(".NS", "")
        return {
            "Ticker": base_ticker,
            "Sector": None,
            "ScreenVerdict": VERDICT_FAIL_NODATA,
            "FailReasons": None,
            "DataGapReasons": "L1; L2; L3; L4; L5",
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
            "ShareholdingStatus": "Error",
            "ShareholdingAsOnDate": None,
            "ShareholdingRevisionDate": None,
            "OwnershipAnomaly": None,
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
            "HasShareholdingData": False,
            "ShareholdingActionLink": None,
            "Error": str(e),
        }


def build_failure_reason_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    fail_df = df[df["ScreenVerdict"] == VERDICT_FAIL_GENUINE].copy()
    if fail_df.empty:
        return pd.DataFrame()

    reason_counts = {
        "L1 Valuation": int((fail_df["L1_Val"] == False).sum()),
        "L2 Profitability": int((fail_df["L2_Prof"] == False).sum()),
        "L3 Cash Flow": int((fail_df["L3_CF"] == False).sum()),
        "L4 Shareholding": int((fail_df["L4_Share"] == False).sum()),
        "L5 Forensic": int((fail_df["L5_Forensic"] == False).sum()),
    }
    return pd.DataFrame(
        [{"FailureReason": k, "FailCount": v} for k, v in reason_counts.items()]
    ).sort_values(["FailCount", "FailureReason"], ascending=[False, True]).reset_index(drop=True)


# -----------------------------
# SIDEBAR
# -----------------------------
st.sidebar.header("Controls")

min_score = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)
show_datagap = st.sidebar.checkbox(
    "Also show PASS (Data gaps present)",
    value=True,
    help="Include stocks that pass all testable layers but have some missing data fields.",
)

screen_mode = st.sidebar.radio(
    "Screen mode",
    ["Nano/Small cap", "Mid cap", "All cap"],
    index=0,
    help="Non-overlapping practical proxy bands: Nano/Small < ₹5,000 Cr; Mid ₹5,000–<₹20,000 Cr.",
)

max_stocks = st.sidebar.number_input(
    "Final stocks to screen",
    min_value=10,
    max_value=500,
    value=50,
    step=10,
)

feeder_pool_size = st.sidebar.number_input(
    "Feeder pool size from bhavcopy turnover",
    min_value=50,
    max_value=1000,
    value=250,
    step=50,
    help="First take a broader turnover pool, then narrow using market-cap hints before live screening.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE bhavcopy (price universe)")
uploaded_nse_file = st.sidebar.file_uploader(
    "Upload NSE EOD CSV (weekly bhavcopy)",
    type=["csv"],
    key="nse_bhavcopy_upload",
)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE shareholding (L4 ownership)")
uploaded_sh_file = st.sidebar.file_uploader(
    "Upload CF-Shareholding-Pattern CSV",
    type=["csv"],
    key="nse_shareholding_upload",
)

pause_between_calls = st.sidebar.slider(
    "Pause between API calls (seconds)",
    min_value=0.0, max_value=1.0, value=0.2, step=0.1,
)

st.sidebar.write(f"Default universe: {len(DEFAULT_UNIVERSE)} tickers")
st.sidebar.write(f"Ticker→Company map: {len(TICKER_TO_COMPANY)} entries")

with st.expander("Cap mode note", expanded=False):
    st.markdown(
        "- Official India classification is SEBI/AMFI rank-based, not fixed rupee bands.\n"
        "- This app uses practical non-overlapping rupee proxies:\n"
        f"  - Nano/Small cap: below ₹{CONFIG['nano_small_max_cr']:.0f} Cr\n"
        f"  - Mid cap: ₹{CONFIG['mid_min_cr']:.0f} Cr to below ₹{CONFIG['mid_max_cr']:.0f} Cr\n"
        "  - All cap: no cap filter"
    )

st.subheader("Fundamentals master")
with st.expander("Show fundamentals_master.csv", expanded=False):
    fundamentals_df = load_fundamentals_master()
    if fundamentals_df.empty:
        st.info("fundamentals_master.csv not found or empty.")
    else:
        st.write(f"Loaded {len(fundamentals_df)} stock(s)")
        st.dataframe(fundamentals_df, use_container_width=True)

st.subheader("Stock master (sector & subsector)")
with st.expander("Show stock_master.csv", expanded=False):
    stock_master_df = load_stock_master()
    if stock_master_df.empty:
        st.info("stock_master.csv not found or empty.")
    else:
        st.write(f"Loaded {len(stock_master_df)} stock(s)")
        st.dataframe(stock_master_df, use_container_width=True)

st.subheader("NSE shareholding data")
with st.expander("Show shareholding CSV preview + match check", expanded=False):
    if uploaded_sh_file is None:
        st.info("No shareholding CSV uploaded. L4 will fall back to yfinance.")
    else:
        try:
            uploaded_sh_file.seek(0)
            sh_preview_df = pd.read_csv(uploaded_sh_file)
            sh_preview_df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in sh_preview_df.columns]
            st.write(f"Loaded {len(sh_preview_df)} rows, {len(sh_preview_df.columns)} columns.")
            st.dataframe(sh_preview_df.head(5), use_container_width=True)
            test_lookup = build_shareholding_lookup(sh_preview_df)
            matched = sorted([t for t in TICKER_TO_COMPANY if t in test_lookup])
            unmatched = sorted([t for t in TICKER_TO_COMPANY if t not in test_lookup])
            if matched:
                st.success(f"✅ Matched {len(matched)} ticker(s): {matched}")
            if unmatched:
                st.warning(f"⚠️ Unmatched {len(unmatched)} ticker(s): {unmatched}")
        except Exception as e:
            st.error(f"Error reading shareholding CSV: {e}")

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

if st.button("Run live screen"):
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
        st.warning("No shareholding CSV uploaded. L4 will use yfinance insider holdings fallback.")

    fundamentals_master_df = load_fundamentals_master()
    stock_master_df = load_stock_master()
    rebuild_fundamentals_lookup(fundamentals_master_df)

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
        feeder_df = equity_universe_df_local.head(int(feeder_pool_size)).copy()
        st.info(f"Using feeder universe: top {len(feeder_df)} stock(s) by NSE turnover.")

        feeder_df = add_mcap_hint_from_masters(feeder_df, stock_master_df, fundamentals_master_df)

        if screen_mode == "Nano/Small cap":
            hinted = feeder_df[
                feeder_df["MCapHint_Cr"].notna() &
                (pd.to_numeric(feeder_df["MCapHint_Cr"], errors="coerce") < CONFIG["nano_small_max_cr"])
            ].copy()
            if not hinted.empty:
                unknown = feeder_df[feeder_df["MCapHint_Cr"].isna()].copy()
                feeder_df = pd.concat([hinted, unknown], ignore_index=True).drop_duplicates(subset=["Ticker"])
                st.info(
                    f"Feeder narrowed by market-cap hints for Nano/Small cap: "
                    f"{len(hinted)} hinted names below ₹{CONFIG['nano_small_max_cr']:.0f} Cr."
                )

        elif screen_mode == "Mid cap":
            hinted = feeder_df[
                feeder_df["MCapHint_Cr"].notna() &
                (pd.to_numeric(feeder_df["MCapHint_Cr"], errors="coerce") >= CONFIG["mid_min_cr"]) &
                (pd.to_numeric(feeder_df["MCapHint_Cr"], errors="coerce") < CONFIG["mid_max_cr"])
            ].copy()
            if not hinted.empty:
                unknown = feeder_df[feeder_df["MCapHint_Cr"].isna()].copy()
                feeder_df = pd.concat([hinted, unknown], ignore_index=True).drop_duplicates(subset=["Ticker"])
                st.info(
                    f"Feeder narrowed by market-cap hints for Mid cap: "
                    f"{len(hinted)} hinted names in ₹{CONFIG['mid_min_cr']:.0f}–<₹{CONFIG['mid_max_cr']:.0f} Cr."
                )

        base_universe = feeder_df.head(int(max_stocks)).copy()
        universe_tickers = base_universe["Ticker"].astype(str).str.upper().tolist()
        tickers_to_screen = [f"{t}.NS" for t in universe_tickers]
        st.info(f"Final live-screen universe: {len(tickers_to_screen)} stock(s).")
    else:
        tickers_to_screen = DEFAULT_UNIVERSE
        st.warning("No NSE bhavcopy uploaded; falling back to DEFAULT_UNIVERSE list.")

    st.write(f"Found {len(tickers_to_screen)} stocks to screen")

    rows: List[Dict[str, Any]] = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_tickers = len(tickers_to_screen)

    with st.spinner("Fetching live data from Yahoo Finance..."):
        for i, ticker in enumerate(tickers_to_screen):
            status_text.text(f"Screening {ticker} ({i+1}/{total_tickers})...")
            row = evaluate_stock(ticker, screen_mode)
            if row:
                rows.append(row)
            progress_bar.progress((i + 1) / total_tickers)
            time.sleep(pause_between_calls)

    status_text.empty()
    progress_bar.empty()

    df = pd.DataFrame(rows)
    screened_count = len(df)

    if not df.empty and stock_master_df is not None and not stock_master_df.empty:
        merge_cols = [c for c in ["Ticker", "Sector", "SubSector"] if c in stock_master_df.columns]
        if len(merge_cols) > 1:
            df = df.merge(stock_master_df[merge_cols], on="Ticker", how="left", suffixes=("", "_stock"))
            if "Sector_stock" in df.columns:
                df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
                df.drop(columns=["Sector_stock"], inplace=True)
            if "SubSector_stock" in df.columns:
                df.rename(columns={"SubSector_stock": "SubSector"}, inplace=True)

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
        df = df.merge(fundamentals_master_df[fund_merge_cols], on="Ticker", how="left", suffixes=("", "_fund"))

    if screen_mode == "Nano/Small cap":
        pre_cap_count = len(df)
        df = df[df["MCap_Cr"].notna() & (df["MCap_Cr"] < CONFIG["nano_small_max_cr"])].copy()
        st.info(
            f"Screen mode applied: Nano/Small cap (< ₹{CONFIG['nano_small_max_cr']:.0f} Cr) — "
            f"{len(df)} of {pre_cap_count} screened stocks remain."
        )
    elif screen_mode == "Mid cap":
        pre_cap_count = len(df)
        df = df[
            df["MCap_Cr"].notna() &
            (df["MCap_Cr"] >= CONFIG["mid_min_cr"]) &
            (df["MCap_Cr"] < CONFIG["mid_max_cr"])
        ].copy()
        st.info(
            f"Screen mode applied: Mid cap (₹{CONFIG['mid_min_cr']:.0f}–<₹{CONFIG['mid_max_cr']:.0f} Cr) — "
            f"{len(df)} of {pre_cap_count} screened stocks remain."
        )
    else:
        st.info("Screen mode applied: All cap — no market-cap filter.")

    summary_df_before_pass_filter = df.copy()

    if only_pass:
        if show_datagap:
            df = df[df["ScreenVerdict"].isin([VERDICT_PASS, VERDICT_PASS_DATAGAP])].copy()
        else:
            df = df[df["ScreenVerdict"] == VERDICT_PASS].copy()

    if min_score > 0:
        df = df[df["Conviction"] >= min_score].copy()

    verdict_order = {
        VERDICT_PASS: 0,
        VERDICT_PASS_DATAGAP: 1,
        VERDICT_FAIL_GENUINE: 2,
        VERDICT_FAIL_NODATA: 3,
    }

    if not df.empty:
        df["_vsort"] = df["ScreenVerdict"].map(verdict_order).fillna(9).astype(int)
        df = df.sort_values(["_vsort", "WeightedScore", "Conviction"], ascending=[True, False, False])
        df.drop(columns=["_vsort"], inplace=True)

    preferred_order = [
        "Ticker", "Sector", "SubSector", "ScreenVerdict", "FailReasons", "DataGapReasons",
        "Price", "MCap_Cr", "PE", "PB", "PEG", "ROCE_pct", "ROE_pct", "ROA_pct", "OPM_pct",
        "RevGrowth_pct", "EarnGrowth_pct", "OCF_PAT", "FCFYield_pct", "YahooInsider_pct",
        "PromoterPct_NSE", "PublicPct_NSE", "EmployeeTrustPct_NSE", "OwnershipTotalPct",
        "OwnershipDataValid", "ShareholdingStatus", "ShareholdingAsOnDate", "ShareholdingRevisionDate",
        "OwnershipAnomaly", "QualityScore_raw", "L1_Val", "L2_Prof", "L3_CF", "L4_Share",
        "L5_Forensic", "L1_DataMissing", "L2_DataMissing", "L3_DataMissing", "L4_DataMissing",
        "L5_DataMissing", "Conviction", "WeightedScore", "Pass", "HasFundamentals",
        "HasShareholdingData", "Latest_Year", "ROE_Latest", "ROCE_Latest", "OPM_Latest",
        "NPM_Latest", "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears", "ROCE_5Y_Avg",
        "ROE_5Y_Avg", "OPM_5Y_Avg", "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag",
        "Reg_Risk_Flag", "Gov_Risk_Flag", "Error", "ShareholdingActionLink",
    ]
    if not df.empty:
        existing_cols = [c for c in preferred_order if c in df.columns]
        remaining_cols = [c for c in df.columns if c not in existing_cols]
        df = df[existing_cols + remaining_cols]

    total = len(df)
    n_pass = int((df["ScreenVerdict"] == VERDICT_PASS).sum()) if not df.empty else 0
    n_datagap = int((df["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum()) if not df.empty else 0
    n_genuine = int((df["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum()) if not df.empty else 0
    n_nodata = int((df["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum()) if not df.empty else 0

    st.success(f"Screen complete — {screened_count} screened, {total} stock(s) shown")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("PASS", n_pass)
    col2.metric("PASS (Data gaps)", n_datagap)
    col3.metric("FAIL (Genuine)", n_genuine)
    col4.metric("FAIL (No data)", n_nodata)

    failure_reason_summary = build_failure_reason_summary(summary_df_before_pass_filter)
    if not failure_reason_summary.empty:
        st.subheader("Failure reason summary")
        st.dataframe(failure_reason_summary, use_container_width=True)

    if not summary_df_before_pass_filter.empty:
        fail_detail = summary_df_before_pass_filter[
            summary_df_before_pass_filter["ScreenVerdict"] == VERDICT_FAIL_GENUINE
        ][["Ticker", "MCap_Cr", "Conviction", "WeightedScore", "FailReasons", "DataGapReasons"]].copy()
        if not fail_detail.empty:
            st.subheader("Failed names summary")
            st.dataframe(
                fail_detail.sort_values(["WeightedScore", "Conviction"], ascending=[False, False]),
                use_container_width=True
            )

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

else:
    st.info("Click **Run live screen** to start.")
