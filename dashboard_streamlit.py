"""
100X Screener — Phase 4
=======================
New in Phase 4 (on top of all Phase 3 features):
  1. Watchlist  — add/remove PASS stocks; track price-at-add vs live price
  2. Score History — per-run snapshot stored in session_state; trend table
  3. Manual Notes — free-text notes per ticker stored in session_state
  4. Deep-Dive Panel — click a ticker for full metric breakdown with layer-by-layer explanation
  5. Session Alerts — flags when PromoterPct < 30% or WeightedScore drops >10 pts vs last run
  6. Sector Heatmap — sector-wise pass rate and avg WeightedScore (st.dataframe styled)
  7. Export Enhancements — date-stamped CSVs for full results AND watchlist separately

Single-file Streamlit app. No localStorage (blocked in sandboxed iframes).
All persistence is in st.session_state (lives for the browser session).
"""

import time
import datetime
from typing import Dict, Any, List, Optional

import pandas as pd
import streamlit as st
import yfinance as yf

# ──────────────────────────────────────────────────────────────
# PAGE CONFIG  (must be first Streamlit call)
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="100X Screener Phase 4 — Indian Equities",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────
# TICKER → NSE COMPANY NAME MAP  (for shareholding CSV matching)
# ──────────────────────────────────────────────────────────────
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
    "SANDUMA":   "Sandur Manganese & Iron Ores Limited",
    "ECLERX":    "eClerx Services Limited",
    "INFOBEAN":  "InfoBeans Technologies Limited",
    "GULPOLY":   "Gujarat Poly Electronics Limited",
    "BLSE":      "BLS International Services Limited",
    "HINDZINC":  "Hindustan Zinc Limited",
}

# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────
DEFAULT_UNIVERSE: List[str] = [
    "LLOYDSME.NS", "POLYCAB.NS", "DEEPAKNTR.NS", "CGPOWER.NS", "TANLA.NS",
    "KPITTECH.NS", "CDSL.NS", "CAMS.NS", "IRCTC.NS", "OLECTRA.NS",
]

CONFIG: Dict[str, Any] = {
    "pe_max":             20.0,
    "peg_max":             1.0,
    "ev_ebitda_max":      12.0,
    "pb_max":              3.0,
    "mcap_min_cr":       200.0,
    "mcap_max_cr":      5000.0,
    "roce_min":           0.20,
    "roe_min":            0.18,
    "roa_min":            0.10,
    "opm_min":            0.15,
    "rev_growth_min":     0.15,
    "earn_growth_min":    0.20,
    "ocf_pat_min":        0.80,
    "fcf_yield_min":      0.03,
    "de_max":             0.50,
    "promoter_min":       0.40,
    "insider_min":        0.40,
    "quality_min_raw":     5,
    "alert_score_drop":   10,
    "alert_promoter_min": 30.0,
}

VERDICT_PASS         = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA  = "FAIL (Insufficient data)"

LAYER_DESCRIPTIONS: Dict[str, str] = {
    "L1_Val":      "L1 Valuation: PE < 20, PEG < 1, EV/EBITDA < 12, PB < 3, MCap 200–5000 Cr (≥3 must pass)",
    "L2_Prof":     "L2 Profitability: ROCE > 20%, ROE > 18%, ROA > 10%, OPM > 15%, Rev growth > 15%, Earn growth > 20% (≥4 must pass)",
    "L3_CF":       "L3 Cash Flow: OCF/PAT > 0.8, FCF Yield > 3%, D/E < 0.5 (≥2 must pass)",
    "L4_Share":    "L4 Shareholding: Promoter ≥ 40% (NSE CSV) or Insider ≥ 40% (yfinance fallback)",
    "L5_Forensic": "L5 Forensic Quality: Piotroski-style score ≥ 5/7",
}

# ──────────────────────────────────────────────────────────────
# SESSION STATE INITIALISATION
# ──────────────────────────────────────────────────────────────
def _init_session_state() -> None:
    defaults = {
        "watchlist":       {},
        "notes":           {},
        "score_history":   [],
        "last_results_df": None,
        "alerts":          [],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_session_state()

# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────
def safe(info: Dict[str, Any], key: str, default=None):
    v = info.get(key, default)
    if v in (None, "N/A", "NaN"):
        return default
    return v


def parse_percent_or_float(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            num = float(text)
        except ValueError:
            return None
    else:
        try:
            num = float(value)
        except (TypeError, ValueError):
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


def load_csv_safe(filename: str) -> pd.DataFrame:
    try:
        return pd.read_csv(filename)
    except Exception:
        return pd.DataFrame()


def rebuild_fundamentals_lookup(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {}
    tmp = df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    return {row["TickerKey"]: row for _, row in tmp.iterrows()}


def build_shareholding_lookup(shareholding_df: pd.DataFrame) -> Dict[str, Dict]:
    lookup: Dict[str, Dict] = {}
    if shareholding_df is None or shareholding_df.empty:
        return lookup

    df = shareholding_df.copy()
    df.columns = [c.strip() for c in df.columns]

    company_col = None
    for candidate in ["Company", "company", "COMPANY", "CompanyName", df.columns[0]]:
        if candidate in df.columns:
            company_col = candidate
            break
    if company_col is None:
        return lookup

    def find_col(cols, *keywords):
        kw = [k.lower() for k in keywords]
        for c in cols:
            if all(k in c.lower() for k in kw):
                return c
        return None

    promoter_col = find_col(df.columns, "promoter") or find_col(df.columns, "promo")
    public_col   = find_col(df.columns, "public")
    emp_col      = find_col(df.columns, "employee") or find_col(df.columns, "trust")

    def norm(s: str) -> str:
        return s.lower().strip()

    company_map: Dict[str, Any] = {norm(str(r[company_col])): r for _, r in df.iterrows()}

    for ticker, company_name in TICKER_TO_COMPANY.items():
        n = norm(company_name)
        row = company_map.get(n)
        if row is None:
            for ckey, crow in company_map.items():
                if n[:20] in ckey or ckey[:20] in n:
                    row = crow
                    break
        if row is None:
            continue

        def pct_val(col):
            if col is None or col not in row.index:
                return None
            try:
                return float(str(row[col]).replace("%", "").strip())
            except Exception:
                return None

        promoter_pct = pct_val(promoter_col)
        public_pct   = pct_val(public_col)
        emp_pct      = pct_val(emp_col)

        as_on_date = None
        for col in df.columns:
            if "as on" in col.lower() or "date" in col.lower():
                try:
                    as_on_date = str(row[col]).strip()
                except Exception:
                    pass
                break

        parts     = [p for p in [promoter_pct, public_pct, emp_pct] if p is not None]
        total_own = round(sum(parts), 2) if parts else None
        valid = (
            promoter_pct is not None
            and public_pct is not None
            and total_own is not None
            and abs(total_own - 100.0) < 5.0
        )

        lookup[ticker] = {
            "PromoterPct_NSE":      promoter_pct,
            "PublicPct_NSE":        public_pct,
            "EmployeeTrustPct_NSE": emp_pct,
            "OwnershipTotalPct":    total_own,
            "OwnershipDataValid":   valid,
            "ShareholdingAsOnDate": as_on_date,
            "HasShareholdingData":  True,
        }
    return lookup


def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()
    df = nse_df.copy()
    for col in ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]:
        if col not in df.columns:
            st.error(f"NSE CSV missing required column: {col}")
            return pd.DataFrame()
    df = df[(df["FinInstrmTp"] == "STK") & (df["SctySrs"] == "EQ")]
    if df.empty:
        return pd.DataFrame()
    df = df[["TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol", "TtlTrfVal"]].copy()
    df = df.rename(columns={
        "TckrSymb": "Ticker", "SctySrs": "Series",
        "ClsPric": "Close", "TtlTradgVol": "Volume", "TtlTrfVal": "Turnover",
    })
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def compute_screen_verdict(l1, l2, l3, l4, l5, l1m, l2m, l3m, l4m, l5m) -> str:
    passes  = [l1, l2, l3, l4, l5]
    missing = [l1m, l2m, l3m, l4m, l5m]
    testable = sum(1 for m in missing if not m)
    if testable < 3:
        return VERDICT_FAIL_NODATA
    genuine_fail = any(not p and not m for p, m in zip(passes, missing))
    if genuine_fail:
        return VERDICT_FAIL_GENUINE
    if any(missing):
        return VERDICT_PASS_DATAGAP
    return VERDICT_PASS


# ──────────────────────────────────────────────────────────────
# CORE EVALUATION
# ──────────────────────────────────────────────────────────────
def evaluate_stock(
    ticker: str,
    fundamentals_lookup: Dict[str, Any],
    shareholding_lookup: Dict[str, Dict],
) -> Dict[str, Any]:
    base = ticker.replace(".NS", "").upper()
    try:
        info     = yf.Ticker(ticker).info
        fund_row = fundamentals_lookup.get(base)
        sh_data  = shareholding_lookup.get(base)

        pe        = safe(info, "trailingPE")
        pb        = safe(info, "priceToBook")
        ev_ebitda = safe(info, "enterpriseToEbitda")
        roe       = safe(info, "returnOnEquity")
        roa       = safe(info, "returnOnAssets")
        opm       = safe(info, "operatingMargins")
        revg      = safe(info, "revenueGrowth")
        earng     = safe(info, "earningsGrowth")
        fcf       = safe(info, "freeCashflow")
        ocf       = safe(info, "operatingCashflow")
        ni        = safe(info, "netIncomeToCommon")
        de        = safe(info, "debtToEquity")
        insider   = safe(info, "heldPercentInsiders")
        mcap_raw  = safe(info, "marketCap") or 0
        price     = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector    = safe(info, "sector", "N/A")
        ebit      = safe(info, "ebit")
        ta        = safe(info, "totalAssets")
        cur_liab  = safe(info, "totalCurrentLiabilities")

        mcap_cr = mcap_raw / 1e7 if mcap_raw else None

        roce = None
        if ebit and ta and cur_liab is not None:
            cap_emp = ta - cur_liab
            if cap_emp > 0:
                roce = ebit / cap_emp

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
            for fm_col, attr in [
                ("ROE_Latest",            "roe"),
                ("ROCE_Latest",           "roce"),
                ("OPM_Latest",            "opm"),
                ("Revenue_CAGR_AllYears", "revg"),
                ("PAT_CAGR_AllYears",     "earng"),
            ]:
                if fm_col in fund_row.index:
                    v = parse_percent_or_float(fund_row[fm_col])
                    if v is not None:
                        if attr == "roe":    roe   = v
                        elif attr == "roce": roce  = v
                        elif attr == "opm":  opm   = v
                        elif attr == "revg": revg  = v
                        elif attr == "earng": earng = v

        promoter_pct_nse = public_pct_nse = emp_trust_pct_nse = None
        ownership_total  = None
        ownership_valid  = False
        has_sh_data      = False
        sh_as_on_date    = None
        sh_status        = "Not available"

        if sh_data is not None:
            promoter_pct_nse  = sh_data.get("PromoterPct_NSE")
            public_pct_nse    = sh_data.get("PublicPct_NSE")
            emp_trust_pct_nse = sh_data.get("EmployeeTrustPct_NSE")
            ownership_total   = sh_data.get("OwnershipTotalPct")
            ownership_valid   = sh_data.get("OwnershipDataValid", False)
            has_sh_data       = sh_data.get("HasShareholdingData", False)
            sh_as_on_date     = sh_data.get("ShareholdingAsOnDate")
            sh_status         = "NSE CSV" if has_sh_data else "Not available"

        if promoter_pct_nse is not None:
            l4_share   = ownership_valid and (promoter_pct_nse / 100.0) >= CONFIG["promoter_min"]
            l4_missing = not has_sh_data
        else:
            l4_share   = insider is not None and insider > CONFIG["insider_min"]
            l4_missing = insider is None
            sh_status  = "yfinance (fallback)" if insider is not None else "Not available"

        l1_chk = [
            pe        is not None and pe        < CONFIG["pe_max"],
            peg       is not None and peg       < CONFIG["peg_max"],
            ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
            pb        is not None and pb        < CONFIG["pb_max"],
            mcap_cr   is not None and CONFIG["mcap_min_cr"] <= mcap_cr <= CONFIG["mcap_max_cr"],
        ]
        l1_avail   = [x is not None for x in [pe, peg, ev_ebitda, pb, mcap_cr]]
        l1_val     = sum(l1_chk) >= 3
        l1_missing = sum(l1_avail) < 3

        l2_chk = [
            roce  is not None and roce  > CONFIG["roce_min"],
            roe   is not None and roe   > CONFIG["roe_min"],
            roa   is not None and roa   > CONFIG["roa_min"],
            opm   is not None and opm   > CONFIG["opm_min"],
            revg  is not None and revg  > CONFIG["rev_growth_min"],
            earng is not None and earng > CONFIG["earn_growth_min"],
        ]
        l2_avail   = [x is not None for x in [roce, roe, roa, opm, revg, earng]]
        l2_prof    = sum(l2_chk) >= 4
        l2_missing = sum(l2_avail) < 4

        l3_chk = [
            ocf_pat   is not None and ocf_pat   > CONFIG["ocf_pat_min"],
            fcf_yield is not None and fcf_yield > CONFIG["fcf_yield_min"],
            de_ratio  is not None and de_ratio  < CONFIG["de_max"],
        ]
        l3_avail   = [x is not None for x in [ocf_pat, fcf_yield, de_ratio]]
        l3_cf      = sum(l3_chk) >= 2
        l3_missing = sum(l3_avail) < 2

        l5_fields = sum([
            safe(info, k) is not None
            for k in ["netIncomeToCommon", "operatingCashflow", "returnOnAssets",
                      "longTermDebt", "totalAssets", "currentRatio", "grossMargins"]
        ])
        l5_forensic = quality_raw >= CONFIG["quality_min_raw"]
        l5_missing  = l5_fields < 4

        conviction = sum([l1_val, l2_prof, l3_cf, l4_share, l5_forensic])

        verdict = compute_screen_verdict(
            l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
            l1_missing, l2_missing, l3_missing, l4_missing, l5_missing,
        )

        ws = 0
        ws += 5  if pe        is not None and pe        < 20   else 0
        ws += 5  if peg       is not None and peg       < 1    else 0
        ws += 5  if ev_ebitda is not None and ev_ebitda < 12   else 0
        ws += 3  if pb        is not None and pb        < 3    else 0
        ws += 2  if mcap_cr   is not None and 200 <= mcap_cr <= 5000 else 0
        ws += 8  if roce      is not None and roce      > 0.20 else 0
        ws += 6  if roe       is not None and roe       > 0.18 else 0
        ws += 4  if roa       is not None and roa       > 0.10 else 0
        ws += 4  if opm       is not None and opm       > 0.15 else 0
        ws += 4  if revg      is not None and revg      > 0.15 else 0
        ws += 4  if earng     is not None and earng     > 0.20 else 0
        ws += 8  if ocf_pat   is not None and ocf_pat   > 0.8  else 0
        ws += 6  if fcf_yield is not None and fcf_yield > 0.03 else 0
        ws += 6  if de_ratio  is not None and de_ratio  < 0.5  else 0
        ws += 5  if l4_share else 0
        ws += min(int(round(10 * quality_raw / 7)), 10)

        ownership_anomaly = None
        if promoter_pct_nse is not None and ownership_valid:
            if promoter_pct_nse < 25.0:
                ownership_anomaly = f"Low promoter: {promoter_pct_nse:.1f}%"
            elif public_pct_nse is not None and public_pct_nse > 70.0:
                ownership_anomaly = f"High public float: {public_pct_nse:.1f}%"

        def _fmt(v, mult=1, dec=2):
            return round(v * mult, dec) if v is not None else None

        return {
            "Ticker":               base,
            "Sector":               sector,
            "ScreenVerdict":        verdict,
            "Price":                price,
            "MCap_Cr":              _fmt(mcap_cr, 1, 1),
            "PE":                   _fmt(pe, 1, 2),
            "PB":                   _fmt(pb, 1, 2),
            "PEG":                  _fmt(peg, 1, 2),
            "ROCE_pct":             _fmt(roce, 100, 1),
            "ROE_pct":              _fmt(roe, 100, 1),
            "ROA_pct":              _fmt(roa, 100, 1),
            "OPM_pct":              _fmt(opm, 100, 1),
            "RevGrowth_pct":        _fmt(revg, 100, 1),
            "EarnGrowth_pct":       _fmt(earng, 100, 1),
            "OCF_PAT":              _fmt(ocf_pat, 1, 2),
            "FCFYield_pct":         _fmt(fcf_yield, 100, 2),
            "YahooInsider_pct":     _fmt(insider, 100, 2),
            "PromoterPct_NSE":      promoter_pct_nse,
            "PublicPct_NSE":        public_pct_nse,
            "EmployeeTrustPct_NSE": emp_trust_pct_nse,
            "OwnershipTotalPct":    ownership_total,
            "OwnershipDataValid":   ownership_valid,
            "ShareholdingStatus":   sh_status,
            "ShareholdingAsOnDate": sh_as_on_date,
            "OwnershipAnomaly":     ownership_anomaly,
            "QualityScore_raw":     quality_raw,
            "L1_Val":               l1_val,
            "L2_Prof":              l2_prof,
            "L3_CF":                l3_cf,
            "L4_Share":             l4_share,
            "L5_Forensic":          l5_forensic,
            "L1_DataMissing":       l1_missing,
            "L2_DataMissing":       l2_missing,
            "L3_DataMissing":       l3_missing,
            "L4_DataMissing":       l4_missing,
            "L5_DataMissing":       l5_missing,
            "Conviction":           conviction,
            "WeightedScore":        ws,
            "Pass":                 verdict in [VERDICT_PASS, VERDICT_PASS_DATAGAP],
            "HasFundamentals":      fund_row is not None,
            "HasShareholdingData":  has_sh_data,
            "Error":                None,
        }

    except Exception as exc:
        return {
            "Ticker": base, "Sector": None,
            "ScreenVerdict": VERDICT_FAIL_NODATA, "Price": None, "MCap_Cr": None,
            "PE": None, "PB": None, "PEG": None,
            "ROCE_pct": None, "ROE_pct": None, "ROA_pct": None, "OPM_pct": None,
            "RevGrowth_pct": None, "EarnGrowth_pct": None,
            "OCF_PAT": None, "FCFYield_pct": None, "YahooInsider_pct": None,
            "PromoterPct_NSE": None, "PublicPct_NSE": None,
            "EmployeeTrustPct_NSE": None, "OwnershipTotalPct": None,
            "OwnershipDataValid": False, "ShareholdingStatus": "Error",
            "ShareholdingAsOnDate": None, "OwnershipAnomaly": None,
            "QualityScore_raw": None,
            "L1_Val": False, "L2_Prof": False, "L3_CF": False,
            "L4_Share": False, "L5_Forensic": False,
            "L1_DataMissing": True, "L2_DataMissing": True, "L3_DataMissing": True,
            "L4_DataMissing": True, "L5_DataMissing": True,
            "Conviction": 0, "WeightedScore": 0, "Pass": False,
            "HasFundamentals": False, "HasShareholdingData": False,
            "Error": str(exc),
        }


# ──────────────────────────────────────────────────────────────
# PHASE 4 HELPERS
# ──────────────────────────────────────────────────────────────
def watchlist_add(ticker: str, price: Optional[float], sector: str) -> None:
    st.session_state.watchlist[ticker] = {
        "add_price": price,
        "add_date":  datetime.date.today().isoformat(),
        "sector":    sector,
    }


def watchlist_remove(ticker: str) -> None:
    st.session_state.watchlist.pop(ticker, None)


def append_score_history(run_ts: str, rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        st.session_state.score_history.append({
            "RunTimestamp":  run_ts,
            "Ticker":        r.get("Ticker"),
            "WeightedScore": r.get("WeightedScore"),
            "Conviction":    r.get("Conviction"),
            "ScreenVerdict": r.get("ScreenVerdict"),
        })


def generate_alerts(current_df: pd.DataFrame, prev_history: List[Dict[str, Any]]) -> List[str]:
    alerts: List[str] = []
    if current_df is None or current_df.empty:
        return alerts

    prev_scores: Dict[str, int] = {}
    for h in prev_history:
        t = h.get("Ticker")
        if t:
            prev_scores[t] = h.get("WeightedScore", 0)

    for _, row in current_df.iterrows():
        ticker = row.get("Ticker", "")
        ws     = row.get("WeightedScore", 0) or 0
        promo  = row.get("PromoterPct_NSE")

        if ticker in prev_scores:
            drop = prev_scores[ticker] - ws
            if drop >= CONFIG["alert_score_drop"]:
                alerts.append(
                    f"⚠️ {ticker}: WeightedScore dropped {drop} pts "
                    f"(was {prev_scores[ticker]}, now {ws})"
                )

        if promo is not None and promo < CONFIG["alert_promoter_min"]:
            alerts.append(
                f"🚨 {ticker}: Promoter holding {promo:.1f}% is below "
                f"{CONFIG['alert_promoter_min']:.0f}% threshold"
            )

    return alerts


def render_deep_dive(row: pd.Series) -> None:
    ticker = row.get("Ticker", "—")
    st.markdown(f"### 🔬 Deep Dive: {ticker}")
    st.caption(f"Sector: {row.get('Sector', 'N/A')}  |  Verdict: **{row.get('ScreenVerdict', '—')}**")

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Price", f"₹{row.get('Price', '—')}")
    col_b.metric("MCap (Cr)", row.get("MCap_Cr", "—"))
    col_c.metric("WeightedScore", row.get("WeightedScore", "—"))

    st.markdown("#### Layer-by-Layer Verdict")
    layers = [
        ("L1_Val",      "L1_DataMissing", "L1 Valuation"),
        ("L2_Prof",     "L2_DataMissing", "L2 Profitability"),
        ("L3_CF",       "L3_DataMissing", "L3 Cash Flow"),
        ("L4_Share",    "L4_DataMissing", "L4 Shareholding"),
        ("L5_Forensic", "L5_DataMissing", "L5 Forensic"),
    ]
    for pass_col, miss_col, label in layers:
        passed  = row.get(pass_col, False)
        missing = row.get(miss_col, False)
        if missing:
            icon, status = "🟡", "Data gap"
        elif passed:
            icon, status = "✅", "PASS"
        else:
            icon, status = "❌", "FAIL"
        desc = LAYER_DESCRIPTIONS.get(pass_col, "")
        st.markdown(f"{icon} **{label}** — {status}  \n*{desc}*")

    st.markdown("#### Key Metrics")
    metrics = {
        "PE": row.get("PE"), "PB": row.get("PB"), "PEG": row.get("PEG"),
        "ROCE %": row.get("ROCE_pct"), "ROE %": row.get("ROE_pct"),
        "ROA %": row.get("ROA_pct"), "OPM %": row.get("OPM_pct"),
        "Rev Growth %": row.get("RevGrowth_pct"), "Earn Growth %": row.get("EarnGrowth_pct"),
        "OCF/PAT": row.get("OCF_PAT"), "FCF Yield %": row.get("FCFYield_pct"),
    }
    m1, m2, m3 = st.columns(3)
    for i, (k, v) in enumerate(metrics.items()):
        [m1, m2, m3][i % 3].metric(k, v if v is not None else "—")

    st.markdown("#### Ownership")
    o1, o2, o3 = st.columns(3)
    o1.metric("Promoter % (NSE)", row.get("PromoterPct_NSE", "—"))
    o2.metric("Public %", row.get("PublicPct_NSE", "—"))
    o3.metric("Data source", row.get("ShareholdingStatus", "—"))
    if row.get("ShareholdingAsOnDate"):
        st.caption(f"As on: {row['ShareholdingAsOnDate']}")
    if row.get("OwnershipAnomaly"):
        st.warning(f"Ownership anomaly: {row['OwnershipAnomaly']}")


def render_sector_heatmap(df: pd.DataFrame) -> None:
    if df is None or df.empty or "Sector" not in df.columns:
        st.info("No sector data available.")
        return

    grp = df.groupby("Sector", dropna=False).agg(
        Total=("Ticker", "count"),
        PassCount=("Pass", "sum"),
        AvgWeightedScore=("WeightedScore", "mean"),
        AvgConviction=("Conviction", "mean"),
    ).reset_index()
    grp["PassRate_%"]       = (grp["PassCount"] / grp["Total"] * 100).round(1)
    grp["AvgWeightedScore"] = grp["AvgWeightedScore"].round(1)
    grp["AvgConviction"]    = grp["AvgConviction"].round(2)
    grp = grp.sort_values("AvgWeightedScore", ascending=False).reset_index(drop=True)

    def color_score(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if v >= 55: return "background-color: #c6efce; color: #276221"
        if v >= 35: return "background-color: #ffeb9c; color: #9c6500"
        return "background-color: #ffc7ce; color: #9c0006"

    def color_passrate(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if v >= 50: return "background-color: #c6efce; color: #276221"
        if v >= 20: return "background-color: #ffeb9c; color: #9c6500"
        return "background-color: #ffc7ce; color: #9c0006"

    def color_conviction(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if v >= 4:   return "background-color: #c6efce; color: #276221"
        if v >= 2.5: return "background-color: #ffeb9c; color: #9c6500"
        return "background-color: #ffc7ce; color: #9c0006"

    styled = (
        grp.style
        .map(color_score,      subset=["AvgWeightedScore"])
        .map(color_passrate,   subset=["PassRate_%"])
        .map(color_conviction, subset=["AvgConviction"])
        .format({
            "AvgWeightedScore": "{:.1f}",
            "AvgConviction":    "{:.2f}",
            "PassRate_%":       "{:.1f}%",
        })
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)


def render_watchlist_table(results_df: Optional[pd.DataFrame]) -> None:
    wl = st.session_state.watchlist
    if not wl:
        st.info("Watchlist is empty. Run a screen and add tickers using the button below the results.")
        return

    rows = []
    for ticker, meta in wl.items():
        add_price  = meta.get("add_price")
        live_price = None
        if results_df is not None and not results_df.empty:
            match = results_df[results_df["Ticker"] == ticker]
            if not match.empty:
                live_price = match.iloc[0].get("Price")
        chg_pct = None
        if add_price and live_price and add_price > 0:
            chg_pct = round((live_price - add_price) / add_price * 100, 2)
        rows.append({
            "Ticker":    ticker,
            "Sector":    meta.get("sector", "—"),
            "Added":     meta.get("add_date", "—"),
            "Price@Add": add_price,
            "Price@Now": live_price,
            "Change_%":  chg_pct,
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    st.markdown("**Remove from watchlist:**")
    remove_ticker = st.selectbox("Select ticker to remove", ["—"] + list(wl.keys()), key="wl_remove")
    if st.button("Remove selected", key="wl_remove_btn"):
        if remove_ticker != "—":
            watchlist_remove(remove_ticker)
            st.success(f"Removed {remove_ticker} from watchlist.")
            st.rerun()


def render_notes_panel() -> None:
    notes = st.session_state.notes
    all_tickers = sorted(notes.keys())
    if st.session_state.last_results_df is not None:
        screened    = st.session_state.last_results_df["Ticker"].tolist()
        all_tickers = sorted(set(all_tickers) | set(screened))

    if not all_tickers:
        st.info("Run a screen first to get a list of tickers.")
        return

    selected = st.selectbox("Ticker", all_tickers, key="notes_ticker")
    existing = notes.get(selected, "")
    new_note = st.text_area("Note", value=existing, height=120, key="notes_text")
    if st.button("Save note", key="notes_save"):
        st.session_state.notes[selected] = new_note
        st.success(f"Note saved for {selected}.")

    if notes:
        st.markdown("**All saved notes:**")
        for t, n in notes.items():
            if n.strip():
                with st.expander(t):
                    st.write(n)


def render_score_history() -> None:
    hist = st.session_state.score_history
    if not hist:
        st.info("No history yet. Run a screen to start building score history.")
        return
    hist_df  = pd.DataFrame(hist)
    tickers  = hist_df["Ticker"].dropna().unique().tolist()
    selected = st.selectbox("Filter by ticker", ["All"] + sorted(tickers), key="hist_ticker")
    view_df  = hist_df if selected == "All" else hist_df[hist_df["Ticker"] == selected]
    st.dataframe(view_df.sort_values(["Ticker", "RunTimestamp"]), use_container_width=True)

    if len(hist_df) > 1:
        pivot = hist_df.pivot_table(
            index="RunTimestamp", columns="Ticker",
            values="WeightedScore", aggfunc="first",
        )
        st.markdown("**WeightedScore over runs:**")
        st.dataframe(pivot, use_container_width=True)


# ──────────────────────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────────────────────
st.sidebar.header("Controls")
st.sidebar.caption(f"yfinance {yf.__version__}")

min_score    = st.sidebar.slider("Min conviction score", 0, 5, 4)
only_pass    = st.sidebar.checkbox("Show only PASS verdicts", value=True)
show_datagap = st.sidebar.checkbox("Include PASS (Data gaps)", value=True)
max_stocks   = st.sidebar.number_input(
    "Max stocks to screen", min_value=10, max_value=500, value=50, step=10,
)

screen_mode = st.sidebar.radio(
    "Screen mode",
    ["Curated mapped universe", "All cap"],
    index=0,
    help="Curated mapped universe uses TICKER_TO_COMPANY list. All cap uses NSE bhavcopy universe.",
)

pause_sec = st.sidebar.slider("Pause between API calls (s)", 0.0, 1.0, 0.2, 0.1)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE price data")
uploaded_nse = st.sidebar.file_uploader(
    "NSE EOD Bhavcopy CSV", type=["csv"], key="nse_upload",
)

st.sidebar.markdown("---")
st.sidebar.subheader("NSE shareholding data")
uploaded_sh = st.sidebar.file_uploader(
    "NSE Shareholding Pattern CSV", type=["csv"], key="sh_upload",
)

st.sidebar.markdown("---")
st.sidebar.write(f"Mapped tickers (Mid/Small cap universe): {len(TICKER_TO_COMPANY)}")
st.sidebar.write(f"Default universe size: {len(DEFAULT_UNIVERSE)}")

# ──────────────────────────────────────────────────────────────
# MAIN TITLE
# ──────────────────────────────────────────────────────────────
st.title("100X Screener — Phase 4 · Indian Equity Live Screener")
st.caption(
    "Phase 4 adds: Watchlist · Score History · Manual Notes · "
    "Deep-Dive Panel · Session Alerts · Sector Heatmap · Enhanced Exports"
)

# ──────────────────────────────────────────────────────────────
# TABS
# ──────────────────────────────────────────────────────────────
tab_screen, tab_watchlist, tab_history, tab_notes, tab_about = st.tabs([
    "📊 Screener", "⭐ Watchlist", "📈 Score History", "📝 Notes", "ℹ️ About",
])

# ══════════════════════════════════════════════════════════════
# TAB 1 — SCREENER
# ══════════════════════════════════════════════════════════════
with tab_screen:

    if st.session_state.alerts:
        with st.expander(f"🔔 Session Alerts ({len(st.session_state.alerts)})", expanded=True):
            for a in st.session_state.alerts:
                st.warning(a)

    with st.expander("ScreenVerdict legend"):
        st.markdown("""
| Verdict | Meaning |
|---|---|
| **PASS** | All 5 layers pass, no data gaps |
| **PASS (Data gaps present)** | Passes every testable layer; some fields unavailable |
| **FAIL (Genuine)** | Fails at least one layer where real data is available |
| **FAIL (Insufficient data)** | Fewer than 3 layers testable |
""")

    with st.expander("L4 Ownership source priority"):
        st.markdown("""
1. **NSE shareholding CSV** (sidebar) — promoter ≥ 40%. Most reliable.
2. **yfinance `heldPercentInsiders`** — fallback; treat with caution.
""")

    with st.expander("Fundamentals master preview"):
        fm_preview = load_csv_safe("fundamentals_master.csv")
        if fm_preview.empty:
            st.info("fundamentals_master.csv not found.")
        else:
            st.write(f"{len(fm_preview)} rows loaded")
            st.dataframe(fm_preview, use_container_width=True)

    with st.expander("NSE shareholding CSV preview"):
        if uploaded_sh is None:
            st.info("Upload NSE shareholding CSV in the sidebar to enable reliable L4.")
        else:
            try:
                uploaded_sh.seek(0)
                sh_preview = pd.read_csv(uploaded_sh)
                st.write(f"{len(sh_preview)} rows, {len(sh_preview.columns)} columns")
                st.dataframe(sh_preview.head(10), use_container_width=True)
                test_lk   = build_shareholding_lookup(sh_preview)
                matched   = [t for t in TICKER_TO_COMPANY if t in test_lk]
                unmatched = [t for t in TICKER_TO_COMPANY if t not in test_lk]
                st.success(f"Matched: {matched}")
                if unmatched:
                    st.warning(f"Unmatched: {unmatched}")
            except Exception as e:
                st.error(f"Error reading shareholding CSV: {e}")

    with st.expander("NSE price CSV preview"):
        if uploaded_nse is None:
            st.info("Upload NSE bhavcopy CSV to use All cap mode.")
        else:
            try:
                uploaded_nse.seek(0)
                nse_prev = pd.read_csv(uploaded_nse)
                eq_univ  = build_nse_equity_universe(nse_prev)
                st.write(f"Equity universe: {len(eq_univ)} stocks. Top 20:")
                st.dataframe(eq_univ.head(20), use_container_width=True)
            except Exception as e:
                st.error(f"Error reading NSE CSV: {e}")

    st.markdown("---")

    if st.button("▶ Run Live Screen", type="primary"):

        # ── Build shareholding lookup ──────────────────────────
        sh_lookup: Dict[str, Dict] = {}
        if uploaded_sh is not None:
            try:
                uploaded_sh.seek(0)
                sh_raw    = pd.read_csv(uploaded_sh)
                sh_lookup = build_shareholding_lookup(sh_raw)
                st.info(f"Shareholding lookup: {len(sh_lookup)} matched — {list(sh_lookup.keys())}")
            except Exception as e:
                st.warning(f"Shareholding lookup error: {e}")
        else:
            st.warning("No shareholding CSV → L4 uses yfinance fallback.")

        # ── Apply screen mode to CONFIG ────────────────────────
        if screen_mode == "Curated mapped universe":
            CONFIG["mcap_min_cr"] = 0.0
            CONFIG["mcap_max_cr"] = 500000.0
        else:
            CONFIG["mcap_min_cr"] = 200.0
            CONFIG["mcap_max_cr"] = 500000.0

        # ── Build universe ─────────────────────────────────────
        tickers_to_screen: List[str] = []

        if screen_mode == "Curated mapped universe":
            mapped = [t for t in TICKER_TO_COMPANY.keys() if t and t != "NAN"]
            tickers_to_screen = [f"{t}.NS" for t in mapped[: int(max_stocks)]]
            st.info(
                f"Universe: TICKER_TO_COMPANY mapped list "
                f"({len(tickers_to_screen)} tickers) — mode: {screen_mode}"
            )
        else:
            if uploaded_nse is not None:
                try:
                    uploaded_nse.seek(0)
                    nse_raw = pd.read_csv(uploaded_nse)
                    eq_univ = build_nse_equity_universe(nse_raw)
                    if not eq_univ.empty:
                        top_t = eq_univ.head(int(max_stocks))["Ticker"].astype(str).str.upper().tolist()
                        tickers_to_screen = [f"{t}.NS" for t in top_t]
                        st.info(
                            f"Universe: top {len(tickers_to_screen)} by NSE turnover "
                            f"— mode: {screen_mode}"
                        )
                except Exception as e:
                    st.error(f"Error rebuilding universe: {e}")

            if not tickers_to_screen:
                tickers_to_screen = DEFAULT_UNIVERSE
                st.warning(
                    f"NSE universe unavailable — using DEFAULT_UNIVERSE "
                    f"({len(DEFAULT_UNIVERSE)} tickers)."
                )

        # ── Load static CSVs ───────────────────────────────────
        fm_df2      = load_csv_safe("fundamentals_master.csv")
        sm_df       = load_csv_safe("stock_master.csv")
        fund_lookup = rebuild_fundamentals_lookup(fm_df2)

        # ── Run screen ─────────────────────────────────────────
        run_ts  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        results = []
        prog    = st.progress(0)
        status  = st.empty()
        total   = len(tickers_to_screen)

        with st.spinner("Fetching live market data…"):
            for i, ticker in enumerate(tickers_to_screen):
                status.text(f"{ticker}  ({i+1}/{total})")
                results.append(evaluate_stock(ticker, fund_lookup, sh_lookup))
                prog.progress((i + 1) / total)
                time.sleep(pause_sec)

        status.empty()
        prog.empty()
        df = pd.DataFrame(results)

        # ── Merge stock_master sector/subsector ────────────────
        if not df.empty and not sm_df.empty:
            sm_cols = [c for c in ["Ticker", "Sector", "SubSector"] if c in sm_df.columns]
            if sm_cols:
                df = df.merge(sm_df[sm_cols], on="Ticker", how="left", suffixes=("", "_sm"))
                if "Sector_sm" in df.columns:
                    df["Sector"] = df["Sector_sm"].combine_first(df["Sector"])
                    df.drop(columns=["Sector_sm"], inplace=True)
                if "SubSector_sm" in df.columns:
                    df.rename(columns={"SubSector_sm": "SubSector"}, inplace=True)

        # ── Merge fundamentals_master extra columns ────────────
        if not df.empty and not fm_df2.empty:
            fm_extra = [
                "Ticker", "Latest_Year",
                "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
                "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
                "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
                "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag",
                "Reg_Risk_Flag", "Gov_Risk_Flag",
            ]
            fm_extra = [c for c in fm_extra if c in fm_df2.columns]
            df = df.merge(fm_df2[fm_extra], on="Ticker", how="left", suffixes=("", "_fund"))

        st.session_state.last_results_df = df.copy()
        append_score_history(run_ts, results)

        prev_hist = [h for h in st.session_state.score_history if h.get("RunTimestamp") != run_ts]
        st.session_state.alerts = generate_alerts(df, prev_hist)

        # ── Filter for display ─────────────────────────────────
        display_df = df.copy()
        if only_pass:
            valid_v = [VERDICT_PASS] + ([VERDICT_PASS_DATAGAP] if show_datagap else [])
            display_df = display_df[display_df["ScreenVerdict"].isin(valid_v)]
        if min_score > 0:
            display_df = display_df[display_df["Conviction"] >= min_score]

        v_order = {
            VERDICT_PASS: 0, VERDICT_PASS_DATAGAP: 1,
            VERDICT_FAIL_GENUINE: 2, VERDICT_FAIL_NODATA: 3,
        }
        display_df["_vs"] = display_df["ScreenVerdict"].map(v_order).fillna(9).astype(int)
        display_df = display_df.sort_values(
            ["_vs", "WeightedScore", "Conviction"], ascending=[True, False, False]
        ).drop(columns=["_vs"])

        pref = [
            "Ticker", "Sector", "SubSector", "ScreenVerdict", "Price", "MCap_Cr",
            "PE", "PB", "PEG", "ROCE_pct", "ROE_pct", "ROA_pct", "OPM_pct",
            "RevGrowth_pct", "EarnGrowth_pct", "OCF_PAT", "FCFYield_pct",
            "YahooInsider_pct", "PromoterPct_NSE", "PublicPct_NSE",
            "EmployeeTrustPct_NSE", "OwnershipTotalPct", "OwnershipDataValid",
            "ShareholdingStatus", "ShareholdingAsOnDate", "OwnershipAnomaly",
            "QualityScore_raw", "L1_Val", "L2_Prof", "L3_CF", "L4_Share", "L5_Forensic",
            "L1_DataMissing", "L2_DataMissing", "L3_DataMissing",
            "L4_DataMissing", "L5_DataMissing",
            "Conviction", "WeightedScore", "Pass",
            "HasFundamentals", "HasShareholdingData",
            "Latest_Year", "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
            "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
            "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
            "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag", "Reg_Risk_Flag", "Gov_Risk_Flag",
            "Error",
        ]
        existing = [c for c in pref if c in display_df.columns]
        leftover = [c for c in display_df.columns if c not in existing]
        display_df = display_df[existing + leftover]

        n_pass    = (df["ScreenVerdict"] == VERDICT_PASS).sum()
        n_datagap = (df["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum()
        n_genuine = (df["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum()
        n_nodata  = (df["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum()

        st.success(f"Screen complete — {len(df)} screened, {len(display_df)} shown")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("PASS", n_pass)
        c2.metric("PASS (Data gaps)", n_datagap)
        c3.metric("FAIL (Genuine)", n_genuine)
        c4.metric("FAIL (No data)", n_nodata)

        if st.session_state.alerts:
            st.warning(f"🔔 {len(st.session_state.alerts)} alert(s) — see banner at top.")

        st.dataframe(display_df, use_container_width=True, height=420)

        date_tag = datetime.date.today().isoformat()
        ex1, ex2 = st.columns(2)
        with ex1:
            st.download_button(
                "⬇ Download Full Results CSV",
                data=display_df.to_csv(index=False),
                file_name=f"100x_screen_results_{date_tag}.csv",
                mime="text/csv",
            )
        with ex2:
            wl_tickers = list(st.session_state.watchlist.keys())
            if wl_tickers and not df.empty:
                wl_export = df[df["Ticker"].isin(wl_tickers)]
                st.download_button(
                    "⬇ Download Watchlist CSV",
                    data=wl_export.to_csv(index=False),
                    file_name=f"100x_watchlist_{date_tag}.csv",
                    mime="text/csv",
                )

        st.markdown("---")
        st.markdown("**Add tickers to watchlist:**")
        pass_tickers = display_df[
            display_df["ScreenVerdict"].isin([VERDICT_PASS, VERDICT_PASS_DATAGAP])
        ]["Ticker"].tolist()
        if pass_tickers:
            wl_pick = st.selectbox("Select PASS ticker", ["—"] + pass_tickers, key="wl_add_pick")
            if st.button("Add to watchlist ⭐", key="wl_add_btn"):
                if wl_pick != "—":
                    match = display_df[display_df["Ticker"] == wl_pick]
                    p = match.iloc[0].get("Price") if not match.empty else None
                    s = match.iloc[0].get("Sector", "—") if not match.empty else "—"
                    watchlist_add(wl_pick, p, s)
                    st.success(f"Added {wl_pick} to watchlist at ₹{p}.")
        else:
            st.info("No PASS stocks in current results to add.")

        st.markdown("---")
        st.markdown("**Deep Dive — select a ticker:**")
        deep_pick = st.selectbox("Ticker", ["—"] + display_df["Ticker"].tolist(), key="deep_pick")
        if deep_pick != "—":
            row_m = display_df[display_df["Ticker"] == deep_pick]
            if not row_m.empty:
                render_deep_dive(row_m.iloc[0])

        st.markdown("---")
        st.markdown("### 🗺 Sector Heatmap")
        render_sector_heatmap(df)

        if "OwnershipAnomaly" in display_df.columns:
            anom = display_df[display_df["OwnershipAnomaly"].notna()][
                ["Ticker", "PromoterPct_NSE", "OwnershipAnomaly"]
            ]
            if not anom.empty:
                st.warning("Ownership anomalies:")
                st.dataframe(anom, use_container_width=True)

    else:
        st.info("Configure options in the sidebar, then click **▶ Run Live Screen**.")
        if st.session_state.last_results_df is not None:
            prev_df = st.session_state.last_results_df
            st.markdown("**Previous run — Deep Dive:**")
            prev_pick = st.selectbox("Ticker", ["—"] + prev_df["Ticker"].tolist(), key="deep_prev")
            if prev_pick != "—":
                row_m = prev_df[prev_df["Ticker"] == prev_pick]
                if not row_m.empty:
                    render_deep_dive(row_m.iloc[0])
            st.markdown("### 🗺 Sector Heatmap (previous run)")
            render_sector_heatmap(prev_df)


# ══════════════════════════════════════════════════════════════
# TAB 2 — WATCHLIST
# ══════════════════════════════════════════════════════════════
with tab_watchlist:
    st.header("⭐ Watchlist")
    st.caption("PASS tickers you have added. Price delta vs your add price.")
    render_watchlist_table(st.session_state.last_results_df)

    if st.session_state.watchlist:
        wl_rows = [
            {"Ticker": t, "Sector": m.get("sector", "—"),
             "AddDate": m.get("add_date", "—"), "AddPrice": m.get("add_price")}
            for t, m in st.session_state.watchlist.items()
        ]
        st.download_button(
            "⬇ Download Watchlist CSV",
            data=pd.DataFrame(wl_rows).to_csv(index=False),
            file_name=f"100x_watchlist_{datetime.date.today().isoformat()}.csv",
            mime="text/csv",
            key="wl_dl_tab2",
        )


# ══════════════════════════════════════════════════════════════
# TAB 3 — SCORE HISTORY
# ══════════════════════════════════════════════════════════════
with tab_history:
    st.header("📈 Score History")
    st.caption("Resets on browser refresh — no localStorage in sandboxed Streamlit.")
    render_score_history()

    if st.session_state.score_history:
        st.download_button(
            "⬇ Download Score History CSV",
            data=pd.DataFrame(st.session_state.score_history).to_csv(index=False),
            file_name=f"100x_score_history_{datetime.date.today().isoformat()}.csv",
            mime="text/csv",
        )
    if st.button("🗑 Clear score history", key="clear_hist"):
        st.session_state.score_history = []
        st.success("Score history cleared.")
        st.rerun()


# ══════════════════════════════════════════════════════════════
# TAB 4 — NOTES
# ══════════════════════════════════════════════════════════════
with tab_notes:
    st.header("📝 Manual Notes")
    st.caption("Free-text notes per ticker, stored for this browser session.")
    render_notes_panel()


# ══════════════════════════════════════════════════════════════
# TAB 5 — ABOUT
# ══════════════════════════════════════════════════════════════
with tab_about:
    st.header("ℹ️ About — 100X Screener Phase 4")
    st.markdown("""
### What Phase 4 adds (over Phase 3)

| Feature | Description |
|---|---|
| **Watchlist** | Add PASS tickers; track live price vs price-at-add; remove at will |
| **Score History** | Per-run WeightedScore + Conviction snapshots; trend pivot; CSV export |
| **Manual Notes** | Free-text notes per ticker, persisted in session |
| **Deep-Dive Panel** | Full layer-by-layer verdict with descriptions; all key metrics in one view |
| **Session Alerts** | Flags PromoterPct < 30% OR WeightedScore drops ≥10 pts vs prior run |
| **Sector Heatmap** | Sector-wise pass rate % and avg WeightedScore; colour-graded table |
| **Enhanced Exports** | Date-stamped CSVs for full results AND watchlist separately |

### Universe modes
- **Mid/Small cap** — screens tickers in `TICKER_TO_COMPANY` map (edit in script to expand).
- **All cap** — screens top N by NSE turnover using uploaded bhavcopy CSV.

### Session state resets on browser refresh
All watchlist, notes, and score history live in `st.session_state` only.

### Files expected in app directory
- `fundamentals_master.csv` — Ticker, ROE_Latest, ROCE_Latest, OPM_Latest, Revenue_CAGR_AllYears, PAT_CAGR_AllYears, ...
- `stock_master.csv` — Ticker, Sector, SubSector
""")
