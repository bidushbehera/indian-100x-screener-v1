# ============================================================
# 100X Screener V7 — Size-First Universe (Nano / Small / Both)
# ============================================================
import time
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="100X Screener V7 — Size-First", layout="wide")

# ── Market-cap band definitions (in ₹ Crore) ────────────────
MCAP_BANDS = {
    "🔬 Nano only (< ₹500 Cr)": (0, 500),
    "🌱 Small cap only (₹500 – ₹5,000 Cr)": (500, 5_000),
    "🔬+🌱 Nano + Small (< ₹5,000 Cr)": (0, 5_000),
    "📐 Extended – include up to ₹20,000 Cr": (0, 20_000),
}

# ── Shareholding CSV column names ───────────────────────────
SH_COL_COMPANY  = "COMPANY"
SH_COL_PROMOTER = "PROMOTER & PROMOTER GROUP (A)"
SH_COL_PUBLIC   = "PUBLIC (B)"
SH_COL_EMP_TRUST= "SHARES HELD BY EMPLOYEE TRUSTS (C2)"
SH_COL_STATUS   = "STATUS"
SH_COL_AS_ON    = "AS ON DATE"
SH_COL_REVISION = "REVISION DATE"
SH_COL_ACTION   = "ACTION"

# ── Verdict labels ──────────────────────────────────────────
VERDICT_PASS         = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA  = "FAIL (Insufficient data)"

# ── Default screening thresholds ───────────────────────────
CONFIG: Dict[str, Any] = {
    "pe_max":           25.0,
    "peg_max":           1.2,
    "ev_ebitda_max":    15.0,
    "pb_max":            4.0,
    "roce_min":          0.18,
    "roe_min":           0.15,
    "roa_min":           0.08,
    "opm_min":           0.12,
    "rev_growth_min":    0.12,
    "earn_growth_min":   0.15,
    "ocf_pat_min_guard": 0.50,
    "de_max_guard":      1.00,
    "promoter_min":      0.35,
    "insider_min":       0.35,
    "quality_min_guard": 3,
}

# ── Fallback universe if no bhavcopy uploaded ───────────────
DEFAULT_UNIVERSE: List[str] = [
    "MAYURUNIQ.NS", "JAYNECOIND.NS", "BLS.NS",
    "QUESS.NS", "MASTEK.NS", "SUPRIYA.NS",
]

# ── Ticker → full company name map (extend as needed) ───────
TICKER_TO_COMPANY: Dict[str, str] = {
    "MAYURUNIQ":  "Mayur Uniquoters Limited",
    "JAYNECOIND": "Jay Neco Industries Limited",
    "BLS":        "BLS International Services Limited",
    "MASTEK":     "Mastek Limited",
    "SUPRIYA":    "Supriya Lifescience Limited",
    "QUESS":      "Quess Corp Limited",
    "POLYCAB":    "Polycab India Limited",
    "TANLA":      "Tanla Platforms Limited",
    "KPITTECH":   "KPIT Technologies Limited",
    "CDSL":       "Central Depository Services (India) Limited",
    "CAMS":       "Computer Age Management Services Limited",
    "IRCTC":      "Indian Railway Catering And Tourism Corporation Limited",
    "CGPOWER":    "CG Power and Industrial Solutions Limited",
    "DEEPAKNTR":  "Deepak Nitrite Limited",
    "OLECTRA":    "Olectra Greentech Limited",
    "LLOYDSME":   "Lloyds Metals And Energy Limited",
}

# ── Global lookup tables ────────────────────────────────────
fundamentals_lookup: Dict[str, Any] = {}
shareholding_lookup: Dict[str, Dict] = {}

# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

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
    if ni > 0:                           score += 1
    if ocf > 0:                          score += 1
    if roa and roa > 0.05:               score += 1
    if ocf > ni > 0:                     score += 1
    if ta > 0 and (ltd / ta) < 0.3:     score += 1
    if cr and cr > 1.5:                  score += 1
    if gm and gm > 0.2 and rg and rg > 0: score += 1
    return score

# ═══════════════════════════════════════════════════════════
# DATA LOADING  ← MODIFIED: upload-driven, no disk reads
# ═══════════════════════════════════════════════════════════

def load_fundamentals_master(uploaded_fundamentals_file) -> pd.DataFrame:
    """Load fundamentals_master from the browser-uploaded file object."""
    if uploaded_fundamentals_file is None:
        return pd.DataFrame()
    try:
        uploaded_fundamentals_file.seek(0)
        df = pd.read_csv(uploaded_fundamentals_file)
        df.columns = [str(c).strip() for c in df.columns]
        if "Ticker" not in df.columns:
            st.error("Uploaded fundamentals file must contain a 'Ticker' column.")
            return pd.DataFrame()
        df["Ticker"] = (
            df["Ticker"]
            .astype(str)
            .str.upper()
            .str.replace(".NS", "", regex=False)
            .str.strip()
        )
        return df
    except Exception as e:
        st.error(f"Could not read uploaded fundamentals file: {e}")
        return pd.DataFrame()


def auto_expand_fundamentals(
    fundamentals_df: pd.DataFrame,
    target_tickers: List[str],
) -> pd.DataFrame:
    """
    Ensure every ticker in target_tickers has at least a skeleton row
    in fundamentals_df. Tickers already present are untouched.
    New rows are added with all metric columns set to None so the
    screener can still run (yfinance values will fill the gaps).
    """
    expected_columns = [
        "Ticker",
        "Latest_Year",
        "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
        "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
        "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
        "MCap_Cr",
        "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag",
        "Reg_Risk_Flag", "Gov_Risk_Flag",
    ]

    if fundamentals_df is None or fundamentals_df.empty:
        fundamentals_df = pd.DataFrame(columns=expected_columns)

    for col in expected_columns:
        if col not in fundamentals_df.columns:
            fundamentals_df[col] = None

    fundamentals_df["Ticker"] = (
        fundamentals_df["Ticker"]
        .astype(str)
        .str.upper()
        .str.replace(".NS", "", regex=False)
        .str.strip()
    )

    existing = set(fundamentals_df["Ticker"].dropna().tolist())
    missing  = [t for t in target_tickers if t not in existing]

    if missing:
        new_rows = pd.DataFrame([
            {col: (t if col == "Ticker" else None) for col in expected_columns}
            for t in missing
        ])
        fundamentals_df = pd.concat(
            [fundamentals_df, new_rows], ignore_index=True
        )

    fundamentals_df = (
        fundamentals_df
        .drop_duplicates(subset=["Ticker"])
        .sort_values("Ticker")
        .reset_index(drop=True)
    )
    return fundamentals_df


def build_stockmaster_from_tickers(
    tickers: List[str],
    pause_between_calls: float = 0.2,
) -> pd.DataFrame:
    """
    Auto-generate a stockmaster (Ticker, Company, Sector, SubSector)
    by querying yfinance for each base ticker. Called at run-time so
    no disk file is needed.
    """
    rows = []
    for ticker in tickers:
        yf_symbol = f"{ticker}.NS"
        try:
            info     = yf.Ticker(yf_symbol).info
            company  = info.get("longName") or info.get("shortName") or ticker
            sector   = info.get("sector")
            subsector= info.get("industry")
        except Exception:
            company  = ticker
            sector   = None
            subsector= None
        rows.append({
            "Ticker":    ticker,
            "Company":   company,
            "Sector":    sector,
            "SubSector": subsector,
        })
        time.sleep(pause_between_calls)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Ticker"] = df["Ticker"].astype(str).str.upper().str.strip()
        df = (
            df.drop_duplicates(subset=["Ticker"])
            .sort_values("Ticker")
            .reset_index(drop=True)
        )
    return df


def rebuild_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> None:
    global fundamentals_lookup
    fundamentals_lookup = {}
    if (
        fundamentals_master_df is None
        or fundamentals_master_df.empty
        or "Ticker" not in fundamentals_master_df.columns
    ):
        return
    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    fundamentals_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def normalise_company_name(s: str) -> str:
    if s is None:
        return ""
    s = str(s).upper().strip()
    for token in [
        "LIMITED", "LTD", "LIMITED.", "LTD.", "INDIA", "(INDIA)", "INDIAN",
        "PRIVATE", "PVT", "PVT.", "&", ",", ".", "-", "/", "(", ")",
    ]:
        s = s.replace(token, " ")
    return " ".join(s.split())


def pct_val(row: pd.Series, col_name: str) -> Optional[float]:
    if col_name not in row.index:
        return None
    v = row[col_name]
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def build_shareholding_lookup(
    shareholding_df: pd.DataFrame,
    stock_master_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Dict]:
    lookup: Dict[str, Dict] = {}
    if shareholding_df is None or shareholding_df.empty:
        return lookup

    df = shareholding_df.copy()
    df.columns = [str(c).strip().lstrip("\ufeff").strip('"') for c in df.columns]

    if SH_COL_COMPANY not in df.columns:
        st.error(f"Shareholding CSV missing expected column '{SH_COL_COMPANY}'.")
        return lookup

    company_to_row: Dict[str, pd.Series] = {}
    for _, row in df.iterrows():
        cname = str(row[SH_COL_COMPANY]).strip()
        key   = normalise_company_name(cname)
        if key and key not in company_to_row:
            company_to_row[key] = row

    stock_name_to_ticker: Dict[str, str] = {}
    if stock_master_df is not None and not stock_master_df.empty:
        temp = stock_master_df.copy()
        temp.columns = [str(c).strip() for c in temp.columns]
        possible_name_cols = ["Company", "CompanyName", "Company Name", "Name"]
        name_col = next(
            (c for c in possible_name_cols if c in temp.columns), None
        )
        if name_col is not None and "Ticker" in temp.columns:
            for _, row in temp.iterrows():
                t    = str(row["Ticker"]).strip().upper()
                nkey = normalise_company_name(str(row[name_col]).strip())
                if t and nkey and nkey not in stock_name_to_ticker:
                    stock_name_to_ticker[nkey] = t

    for ticker, company_name in TICKER_TO_COMPANY.items():
        nkey = normalise_company_name(company_name)
        if nkey and nkey not in stock_name_to_ticker:
            stock_name_to_ticker[nkey] = ticker

    matched = 0
    for nkey, row in company_to_row.items():
        ticker = stock_name_to_ticker.get(nkey)
        if ticker is None:
            for stock_key, stock_ticker in stock_name_to_ticker.items():
                if len(nkey) >= 8 and (nkey in stock_key or stock_key in nkey):
                    ticker = stock_ticker
                    break
        if ticker is None:
            continue

        promoter_pct  = pct_val(row, SH_COL_PROMOTER)
        public_pct    = pct_val(row, SH_COL_PUBLIC)
        emp_pct       = pct_val(row, SH_COL_EMP_TRUST)
        as_on_date    = str(row[SH_COL_AS_ON]).strip()    if SH_COL_AS_ON    in row.index else None
        revision_date = str(row[SH_COL_REVISION]).strip() if SH_COL_REVISION in row.index else None
        action_link   = str(row[SH_COL_ACTION]).strip()   if SH_COL_ACTION   in row.index else None

        parts     = [p for p in [promoter_pct, public_pct, emp_pct] if p is not None]
        total_own = round(sum(parts), 2) if parts else None

        ownership_valid = (
            promoter_pct is not None
            and public_pct is not None
            and total_own is not None
            and abs(total_own - 100.0) < 5.0
        )

        lookup[ticker] = {
            "PromoterPct_NSE":        promoter_pct,
            "PublicPct_NSE":          public_pct,
            "EmployeeTrustPct_NSE":   emp_pct,
            "OwnershipTotalPct":      total_own,
            "OwnershipDataValid":     ownership_valid,
            "ShareholdingStatus":     "NSE CSV",
            "ShareholdingAsOnDate":   as_on_date,
            "ShareholdingRevisionDate": revision_date,
            "ShareholdingActionLink": action_link,
            "HasShareholdingData":    True,
        }
        matched += 1

    st.info(
        f"Expanded shareholding lookup built: {matched} ticker(s) matched from uploaded CSV."
    )
    return lookup


# ═══════════════════════════════════════════════════════════
# UNIVERSE BUILDER
# ═══════════════════════════════════════════════════════════

def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    """Parse NSE EOD bhavcopy into a clean equity DataFrame sorted by turnover."""
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
        "TckrSymb":   "Ticker",
        "SctySrs":    "Series",
        "ClsPric":    "Close",
        "TtlTradgVol":"Volume",
        "TtlTrfVal":  "Turnover",
    })
    df["Ticker"] = df["Ticker"].astype(str).str.upper()
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def apply_mcap_filter(
    universe_df: pd.DataFrame,
    mcap_min_cr: float,
    mcap_max_cr: float,
    max_stocks: int,
    pause: float,
) -> Tuple[pd.DataFrame, int]:
    """
    Fetch live MCap from Yahoo for bhavcopy tickers and keep only those
    within [mcap_min_cr, mcap_max_cr]. Returns (filtered_df, total_checked).
    """
    kept: List[Dict] = []
    checked = 0

    progress = st.progress(0)
    status   = st.empty()

    tickers_all = universe_df["Ticker"].astype(str).str.upper().tolist()

    for ticker in tickers_all:
        if len(kept) >= max_stocks:
            break
        checked += 1
        try:
            info   = yf.Ticker(f"{ticker}.NS").info
            mc_raw = info.get("marketCap")
            if mc_raw is None:
                continue
            mc_cr = mc_raw / 1e7
            if mcap_min_cr <= mc_cr <= mcap_max_cr:
                kept.append({"Ticker": ticker, "MCap_Cr_Live": round(mc_cr, 1)})
        except Exception:
            pass
        finally:
            progress.progress(min(checked / max(len(tickers_all), 1), 1.0))
            status.text(
                f"MCap filter: checked {checked} | kept {len(kept)} in band "
                f"₹{mcap_min_cr:,.0f}–₹{mcap_max_cr:,.0f} Cr | target {max_stocks}"
            )
        time.sleep(pause)

    progress.empty()
    status.empty()
    return pd.DataFrame(kept), checked


# ═══════════════════════════════════════════════════════════
# SCORING & VERDICT
# ═══════════════════════════════════════════════════════════

def compute_screen_verdict(
    l1_val, l2_prof, l3_guard, l4_share, l5_guard,
    l1_dm, l2_dm, l3_dm, l4_dm, l5_dm,
):
    testable = sum(1 for m in [l1_dm, l2_dm, l3_dm, l4_dm, l5_dm] if not m)
    if testable < 3:
        return VERDICT_FAIL_NODATA
    genuine_fail = any(
        (not p) and (not m)
        for p, m in zip(
            [l1_val, l2_prof, l3_guard, l4_share, l5_guard],
            [l1_dm,  l2_dm,  l3_dm,   l4_dm,    l5_dm],
        )
    )
    if genuine_fail:
        return VERDICT_FAIL_GENUINE
    if any([l1_dm, l2_dm, l3_dm, l4_dm, l5_dm]):
        return VERDICT_PASS_DATAGAP
    return VERDICT_PASS


def get_preset_weights(preset_name: str) -> Tuple[int, int, int, int, int]:
    presets = {
        "Balanced":            (4, 3, 4, 4, 3),
        "Capital preservation":(5, 3, 5, 5, 3),
        "Early compounder":    (5, 2, 4, 4, 3),
        "Turnaround cautious": (3, 2, 5, 5, 2),
        "Fraud avoidance":     (5, 2, 4, 5, 4),
        "Custom":              (4, 3, 4, 4, 3),
    }
    return presets.get(preset_name, (4, 3, 4, 4, 3))


# ═══════════════════════════════════════════════════════════
# STOCK EVALUATOR
# ═══════════════════════════════════════════════════════════

def evaluate_stock(ticker: str, weights: Dict[str, int]) -> Dict[str, Any]:
    try:
        yf_ticker   = yf.Ticker(ticker)
        base_ticker = ticker.replace(".NS", "").upper()
        fund_row    = fundamentals_lookup.get(base_ticker)
        sh_data     = shareholding_lookup.get(base_ticker)
        info        = yf_ticker.info

        pe       = safe(info, "trailingPE")
        pb       = safe(info, "priceToBook")
        ev_ebitda= safe(info, "enterpriseToEbitda")
        roe      = safe(info, "returnOnEquity")
        roa      = safe(info, "returnOnAssets")
        opm      = safe(info, "operatingMargins")
        revg     = safe(info, "revenueGrowth")
        earng    = safe(info, "earningsGrowth")
        fcf      = safe(info, "freeCashflow")
        ocf      = safe(info, "operatingCashflow")
        ni       = safe(info, "netIncomeToCommon")
        de       = safe(info, "debtToEquity")
        insider  = safe(info, "heldPercentInsiders")
        mcap_raw = safe(info, "marketCap") or 0
        price    = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector   = safe(info, "sector", "N/A")
        ebit     = safe(info, "ebit")
        ta       = safe(info, "totalAssets")
        cur_liab = safe(info, "totalCurrentLiabilities")

        mcap_cr = mcap_raw / 1e7 if mcap_raw else None

        mcap_band = "Unknown"
        if mcap_cr is not None:
            if mcap_cr < 500:
                mcap_band = "Nano"
            elif mcap_cr < 5_000:
                mcap_band = "Small"
            elif mcap_cr < 20_000:
                mcap_band = "Mid"
            else:
                mcap_band = "Large"

        roce = None
        if ebit and ta and cur_liab is not None:
            cap_employed = ta - cur_liab
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

        # Override with fundamentals_master if available
        if fund_row is not None:
            for fm_col, var_name in [
                ("ROE_Latest",           "roe"),
                ("ROCE_Latest",          "roce"),
                ("OPM_Latest",           "opm"),
                ("Revenue_CAGR_AllYears","revg"),
                ("PAT_CAGR_AllYears",    "earng"),
            ]:
                if fm_col in fund_row.index:
                    v = parse_percent_or_float(fund_row[fm_col])
                    if v is not None:
                        if var_name == "roe":   roe   = v
                        elif var_name == "roce":roce  = v
                        elif var_name == "opm": opm   = v
                        elif var_name == "revg":revg  = v
                        elif var_name == "earng":earng = v

        # ── Shareholding (L4) ─────────────────────────────
        promoter_pct_nse = public_pct_nse = emp_trust_pct_nse = None
        ownership_total_pct   = None
        ownership_data_valid  = False
        has_shareholding_data = False
        sh_as_on_date = sh_revision_date = sh_action_link = None
        shareholding_status = "Not available"

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
            l4_share       = ownership_data_valid and (promoter_pct_nse / 100.0) >= CONFIG["promoter_min"]
            l4_data_missing= not has_shareholding_data
            ownership_score= 1.0 if l4_share else 0.0
        else:
            l4_share        = insider is not None and insider > CONFIG["insider_min"]
            l4_data_missing = insider is None
            shareholding_status = "yfinance (fallback)" if insider is not None else "Not available"
            ownership_score = 0.75 if l4_share else 0.0

        # ── L1 Valuation ──────────────────────────────────
        l1_checks = [
            pe is not None and pe < CONFIG["pe_max"],
            peg is not None and peg < CONFIG["peg_max"],
            ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
            pb is not None and pb < CONFIG["pb_max"],
            mcap_cr is not None,
        ]
        l1_available    = [pe is not None, peg is not None, ev_ebitda is not None,
                           pb is not None, mcap_cr is not None]
        l1_val          = sum(l1_checks) >= 3
        l1_data_missing = sum(l1_available) < 3

        # ── L2 Profitability ──────────────────────────────
        l2_checks = [
            roce is not None and roce > CONFIG["roce_min"],
            roe  is not None and roe  > CONFIG["roe_min"],
            roa  is not None and roa  > CONFIG["roa_min"],
            opm  is not None and opm  > CONFIG["opm_min"],
            revg is not None and revg > CONFIG["rev_growth_min"],
            earng is not None and earng > CONFIG["earn_growth_min"],
        ]
        l2_available    = [roce is not None, roe is not None, roa is not None,
                           opm is not None, revg is not None, earng is not None]
        l2_prof         = sum(l2_checks) >= 4
        l2_data_missing = sum(l2_available) < 4

        # ── L3 Cash-quality guardrail ─────────────────────
        l3_guard = (
            (ocf_pat is not None and ocf_pat >= CONFIG["ocf_pat_min_guard"])
            and
            (de_ratio is None or de_ratio <= CONFIG["de_max_guard"])
        )
        l3_data_missing = sum([
            ocf_pat   is not None,
            fcf_yield is not None,
            de_ratio  is not None,
        ]) < 2

        # ── L5 Forensic quality ───────────────────────────
        l5_fields_present = sum([
            safe(info, "netIncomeToCommon")     is not None,
            safe(info, "operatingCashflow")     is not None,
            safe(info, "returnOnAssets")        is not None,
            safe(info, "longTermDebt")          is not None,
            safe(info, "totalAssets")           is not None,
            safe(info, "currentRatio")          is not None,
            safe(info, "grossMargins")          is not None,
        ])
        l5_data_missing = l5_fields_present < 4
        l5_guard        = quality_raw >= CONFIG["quality_min_guard"]

        # ── Verdict ───────────────────────────────────────
        verdict = compute_screen_verdict(
            l1_val, l2_prof, l3_guard, l4_share, l5_guard,
            l1_data_missing, l2_data_missing, l3_data_missing,
            l4_data_missing, l5_data_missing,
        )

        # ── Scoring ───────────────────────────────────────
        l3_score = 0.0
        if ocf_pat is not None:
            l3_score += (1.0 if ocf_pat >= 1.2 else 0.8 if ocf_pat >= 0.8 else 0.5 if ocf_pat >= 0.5 else 0.0)
        if fcf_yield is not None:
            l3_score += (1.0 if fcf_yield >= 0.05 else 0.7 if fcf_yield >= 0.03 else 0.4 if fcf_yield > 0 else 0.0)
        if de_ratio is not None:
            l3_score += (1.0 if de_ratio <= 0.3 else 0.8 if de_ratio <= 0.5 else 0.4 if de_ratio <= 1.0 else 0.0)
        l3_score = min(l3_score / 3.0, 1.0)

        l5_score   = min(quality_raw / 7.0, 1.0) if quality_raw is not None else 0.0
        conviction = sum([l1_val, l2_prof, l3_guard, l4_share, l5_guard])
        hard_pass  = bool(l2_prof and l3_guard and l5_guard and conviction >= 4)

        weighted_score = 0.0
        weighted_score += 8 if l1_val else 0
        weighted_score += 14 if l2_prof else 0
        weighted_score += weights["l3_ocf_pat"] * (
            1.0 if ocf_pat is not None and ocf_pat >= 0.8 else
            0.5 if ocf_pat is not None and ocf_pat >= 0.5 else 0.0
        )
        weighted_score += weights["l3_fcf_yield"] * (
            1.0 if fcf_yield is not None and fcf_yield >= 0.03 else
            0.5 if fcf_yield is not None and fcf_yield >  0    else 0.0
        )
        weighted_score += weights["l3_debt"] * (
            1.0 if de_ratio is not None and de_ratio <= 0.5 else
            0.5 if de_ratio is not None and de_ratio <= 1.0 else
            0.0 if de_ratio is not None else 0.25
        )
        weighted_score += weights["l5_forensic"] * l5_score
        weighted_score += weights["ownership"]   * ownership_score

        fail_reasons     = []
        data_gap_reasons = []
        if not l1_val   and not l1_data_missing: fail_reasons.append("L1 Valuation")
        if not l2_prof  and not l2_data_missing: fail_reasons.append("L2 Profitability")
        if not l3_guard and not l3_data_missing: fail_reasons.append("L3 Guardrail")
        if not l4_share and not l4_data_missing: fail_reasons.append("L4 Shareholding")
        if not l5_guard and not l5_data_missing: fail_reasons.append("L5 Guardrail")
        if l1_data_missing: data_gap_reasons.append("L1")
        if l2_data_missing: data_gap_reasons.append("L2")
        if l3_data_missing: data_gap_reasons.append("L3")
        if l4_data_missing: data_gap_reasons.append("L4")
        if l5_data_missing: data_gap_reasons.append("L5")

        conviction_pct = round((conviction / 5.0) * 100, 1)

        return {
            "Ticker":               base_ticker,
            "Sector":               sector,
            "MCap_Band":            mcap_band,
            "ScreenVerdict":        verdict,
            "FailReasons":          "; ".join(fail_reasons)     if fail_reasons     else None,
            "DataGapReasons":       "; ".join(data_gap_reasons) if data_gap_reasons else None,
            "Price":                price,
            "MCap_Cr":              round(mcap_cr, 1) if mcap_cr is not None else None,
            "PE":                   round(pe, 2)      if pe      is not None else None,
            "PB":                   round(pb, 2)      if pb      is not None else None,
            "PEG":                  round(peg, 2)     if peg     is not None else None,
            "ROCE_pct":             round(roce  * 100, 1) if roce  is not None else None,
            "ROE_pct":              round(roe   * 100, 1) if roe   is not None else None,
            "ROA_pct":              round(roa   * 100, 1) if roa   is not None else None,
            "OPM_pct":              round(opm   * 100, 1) if opm   is not None else None,
            "RevGrowth_pct":        round(revg  * 100, 1) if revg  is not None else None,
            "EarnGrowth_pct":       round(earng * 100, 1) if earng is not None else None,
            "OCF_PAT":              round(ocf_pat,  2) if ocf_pat  is not None else None,
            "FCFYield_pct":         round(fcf_yield * 100, 2) if fcf_yield is not None else None,
            "DebtToEquity_raw":     round(de_ratio, 2) if de_ratio is not None else None,
            "YahooInsider_pct":     round(insider * 100, 2) if insider is not None else None,
            "PromoterPct_NSE":      promoter_pct_nse,
            "PublicPct_NSE":        public_pct_nse,
            "EmployeeTrustPct_NSE": emp_trust_pct_nse,
            "OwnershipTotalPct":    ownership_total_pct,
            "OwnershipDataValid":   ownership_data_valid,
            "ShareholdingStatus":   shareholding_status,
            "ShareholdingAsOnDate": sh_as_on_date,
            "ShareholdingRevisionDate": sh_revision_date,
            "QualityScore_raw":     quality_raw,
            "L1_Val":               l1_val,
            "L2_Prof":              l2_prof,
            "L3_Guard":             l3_guard,
            "L5_Guard":             l5_guard,
            "L3_Score_0to1":        round(l3_score, 3),
            "L5_Score_0to1":        round(l5_score, 3),
            "L4_Share":             l4_share,
            "L1_DataMissing":       l1_data_missing,
            "L2_DataMissing":       l2_data_missing,
            "L3_DataMissing":       l3_data_missing,
            "L4_DataMissing":       l4_data_missing,
            "L5_DataMissing":       l5_data_missing,
            "Conviction":           conviction,
            "ConvictionPct":        conviction_pct,
            "WeightedScore":        round(weighted_score, 2),
            "Pass":                 hard_pass,
            "HasFundamentals":      fund_row is not None,
            "HasShareholdingData":  has_shareholding_data,
            "ShareholdingActionLink": sh_action_link,
            "Error":                None,
        }

    except Exception as e:
        base_ticker = ticker.replace(".NS", "")
        return {
            "Ticker": base_ticker, "Sector": None, "MCap_Band": "Unknown",
            "ScreenVerdict": VERDICT_FAIL_NODATA,
            "FailReasons": None, "DataGapReasons": "L1; L2; L3; L4; L5",
            "Price": None, "MCap_Cr": None,
            "PE": None, "PB": None, "PEG": None,
            "ROCE_pct": None, "ROE_pct": None, "ROA_pct": None,
            "OPM_pct": None, "RevGrowth_pct": None, "EarnGrowth_pct": None,
            "OCF_PAT": None, "FCFYield_pct": None, "DebtToEquity_raw": None,
            "YahooInsider_pct": None, "PromoterPct_NSE": None,
            "PublicPct_NSE": None, "EmployeeTrustPct_NSE": None,
            "OwnershipTotalPct": None, "OwnershipDataValid": False,
            "ShareholdingStatus": "Error",
            "ShareholdingAsOnDate": None, "ShareholdingRevisionDate": None,
            "QualityScore_raw": None,
            "L1_Val": False, "L2_Prof": False, "L3_Guard": False,
            "L5_Guard": False, "L3_Score_0to1": 0.0, "L5_Score_0to1": 0.0,
            "L4_Share": False,
            "L1_DataMissing": True, "L2_DataMissing": True,
            "L3_DataMissing": True, "L4_DataMissing": True,
            "L5_DataMissing": True,
            "Conviction": 0, "ConvictionPct": 0.0, "WeightedScore": 0.0, "Pass": False,
            "HasFundamentals": False, "HasShareholdingData": False,
            "ShareholdingActionLink": None, "Error": str(e),
        }


# ═══════════════════════════════════════════════════════════
# SUMMARY HELPERS
# ═══════════════════════════════════════════════════════════

def build_failure_reason_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    fail_df = df[df["ScreenVerdict"] == VERDICT_FAIL_GENUINE].copy()
    if fail_df.empty:
        return pd.DataFrame()
    counts = {
        "L1 Valuation":    int((fail_df["L1_Val"]   == False).sum()),
        "L2 Profitability":int((fail_df["L2_Prof"]  == False).sum()),
        "L3 Guardrail":    int((fail_df["L3_Guard"] == False).sum()),
        "L4 Shareholding": int((fail_df["L4_Share"] == False).sum()),
        "L5 Guardrail":    int((fail_df["L5_Guard"] == False).sum()),
    }
    return (
        pd.DataFrame([{"FailureReason": k, "FailCount": v} for k, v in counts.items()])
        .sort_values(["FailCount", "FailureReason"], ascending=[False, True])
        .reset_index(drop=True)
    )


# ═══════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.title("100X Screener V7 — Size-First Universe")
st.caption(
    "World-class quality parameters · Nano / Small / Extended cap filter · "
    "NSE Bhavcopy + Shareholding CSV + weighted guardrails"
)

# ─── Sidebar ────────────────────────────────────────────────
st.sidebar.header("Universe Controls")

size_choice = st.sidebar.radio(
    "📐 Cap size universe",
    options=list(MCAP_BANDS.keys()),
    index=2,
    help=(
        "Nano < ₹500 Cr | Small ₹500–₹5,000 Cr | "
        "Nano+Small < ₹5,000 Cr | Extended up to ₹20,000 Cr"
    ),
)
mcap_min_cr, mcap_max_cr = MCAP_BANDS[size_choice]

st.sidebar.markdown(
    f"**Active band:** ₹{mcap_min_cr:,.0f} Cr → ₹{mcap_max_cr:,.0f} Cr"
)

st.sidebar.markdown("---")
st.sidebar.header("Screen Controls")

min_score  = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass  = st.sidebar.checkbox("Show only final pass names", value=True)
show_datagap = st.sidebar.checkbox("Also show PASS (Data gaps present)", value=True)
max_stocks = st.sidebar.number_input(
    "Max stocks to collect in size band",
    min_value=10, max_value=1000, value=200, step=25,
    help="The MCap filter walks the bhavcopy in turnover order and stops when it has collected this many tickers inside your chosen size band.",
)
feeder_pool_size = st.sidebar.number_input(
    "Bhavcopy pool to walk (for MCap filter)",
    min_value=100, max_value=5000, value=2000, step=100,
    help="How far into the bhavcopy to walk. Nano caps are less liquid so set this higher (2000–5000).",
)
pause_between_calls = st.sidebar.slider(
    "Pause between API calls (s)", min_value=0.0, max_value=1.0, value=0.3, step=0.1
)

st.sidebar.markdown("---")
st.sidebar.header("Quality Weights")

preset = st.sidebar.selectbox(
    "Quality weighting preset",
    ["Balanced", "Capital preservation", "Early compounder",
     "Turnaround cautious", "Fraud avoidance", "Custom"],
    index=0,
)
default_ocf, default_fcf, default_debt, default_forensic, default_ownership = get_preset_weights(preset)

l3_ocf_pat_w  = st.sidebar.slider("Weight: OCF/PAT",      1, 5, default_ocf)
l3_fcf_yield_w= st.sidebar.slider("Weight: FCF Yield",    1, 5, default_fcf)
l3_debt_w     = st.sidebar.slider("Weight: Debt Quality", 1, 5, default_debt)
l5_forensic_w = st.sidebar.slider("Weight: Forensic Qual.",1, 5, default_forensic)
ownership_w   = st.sidebar.slider("Weight: Ownership",    1, 5, default_ownership)

weights = {
    "l3_ocf_pat":  l3_ocf_pat_w,
    "l3_fcf_yield":l3_fcf_yield_w,
    "l3_debt":     l3_debt_w,
    "l5_forensic": l5_forensic_w,
    "ownership":   ownership_w,
}

st.sidebar.markdown("---")
st.sidebar.header("File Uploads")

# ★ NEW: fundamentals_master uploader
uploaded_fundamentals_file = st.sidebar.file_uploader(
    "Upload fundamentals_master.csv",
    type=["csv"],
    key="fundamentals_master_upload",
    help=(
        "Upload your curated fundamentals_master.csv. "
        "Tickers in the live screen universe that are missing from this file "
        "will be auto-added as blank rows so yfinance data fills the gaps."
    ),
)

uploaded_nse_file = st.sidebar.file_uploader(
    "Upload NSE EOD CSV (weekly bhavcopy)", type=["csv"], key="nse_bhavcopy_upload"
)

uploaded_sh_file = st.sidebar.file_uploader(
    "Upload CF-Shareholding-Pattern CSV", type=["csv"], key="nse_shareholding_upload"
)

# ─── Info expanders ────────────────────────────────────────
with st.expander("ℹ️ Cap size classification used in this screener", expanded=False):
    st.markdown("""
| Band | MCap Range | What you'll find |
|------|-----------|-----------------| 
| 🔬 **Nano** | < ₹500 Cr | Ultra-neglected; high analyst-blind-spot potential; lower liquidity |
| 🌱 **Small** | ₹500 – ₹5,000 Cr | SEBI small-cap definition; reasonable liquidity; 10x+ potential |
| 🔬+🌱 **Nano + Small** | < ₹5,000 Cr | Combined default; widest quality-value hunting ground |
| 📐 **Extended** | < ₹20,000 Cr | Includes quality mid-cap names that are temporarily cheap |

**Why size-first matters:** The feeder universe in V6 used top-500 by NSE turnover, which systematically
excluded nano/small caps (low liquidity = low turnover rank). V7 explicitly filters by MCap band,
so the engine now hunts in the right pond for your mandate.
""")

with st.expander("ℹ️ How file uploads work", expanded=False):
    st.markdown("""
- **fundamentals_master.csv** — your curated file with ROE/ROCE/OPM/CAGR overrides.
  Tickers in the live universe that are missing from this file will be auto-added as
  blank skeleton rows so the screener can still run (yfinance data fills the gaps).
- **NSE EOD CSV (bhavcopy)** — download from NSE India on Friday night. Used to build
  the equity universe sorted by turnover. If not uploaded, the 6-stock DEFAULT_UNIVERSE is used.
- **CF-Shareholding-Pattern CSV** — download from NSE Corporate Filings. Used for L4 promoter
  holding check. If not uploaded, yfinance insider holdings are used as fallback.
""")

with st.expander("ℹ️ How the quality weights work", expanded=False):
    st.markdown("""
- **OCF/PAT high (4–5):** Earnings backed by real operating cash — reduces accounting mirages.
- **FCF Yield high (4–5):** Surplus cash generators. May penalise early reinvestment stories.
- **Debt Quality high (4–5):** Strongest for risk control, especially in nano/small caps.
- **Forensic Quality high (4–5):** Best for fraud/governance risk avoidance.
- **Ownership high (4–5):** Pushes promoter-backed names up — only active when data exists.
""")

# ─── Run button ────────────────────────────────────────────
if st.button("🚀 Run live screen", type="primary"):

    # ── 1. Load fundamentals master from upload ───────────
    fundamentals_master_df = load_fundamentals_master(uploaded_fundamentals_file)
    if fundamentals_master_df.empty:
        st.warning(
            "No fundamentals_master.csv uploaded (or file is empty). "
            "The screener will run on yfinance data only — curated overrides will not be applied."
        )
    else:
        st.info(f"Fundamentals master loaded: {len(fundamentals_master_df)} rows.")

    # ── 2. Build shareholding lookup ──────────────────────
    if uploaded_sh_file is not None:
        try:
            uploaded_sh_file.seek(0)
            sh_raw_df = pd.read_csv(uploaded_sh_file)
            sh_raw_df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in sh_raw_df.columns]
            # Pass a temporary stub stockmaster so the matcher can also use TICKER_TO_COMPANY;
            # the real stockmaster is built later but shareholding matching can still proceed.
            shareholding_lookup = build_shareholding_lookup(sh_raw_df, stock_master_df=None)
            st.info(f"Shareholding lookup ready with {len(shareholding_lookup)} ticker(s).")
        except Exception as e:
            st.warning(f"Could not build shareholding lookup: {e}")
            shareholding_lookup = {}
    else:
        shareholding_lookup = {}
        st.warning("No shareholding CSV uploaded. L4 will use yfinance fallback.")

    # ── 3. Build bhavcopy universe ────────────────────────
    if uploaded_nse_file is not None:
        try:
            uploaded_nse_file.seek(0)
            raw_nse_df             = pd.read_csv(uploaded_nse_file)
            equity_universe_df_local = build_nse_equity_universe(raw_nse_df)
        except Exception as e:
            st.error(f"Error reading NSE bhavcopy: {e}")
            equity_universe_df_local = None
    else:
        equity_universe_df_local = None

    # ── 4. Apply MCap filter to determine screen universe ─
    if equity_universe_df_local is not None and not equity_universe_df_local.empty:
        st.info(
            f"Bhavcopy loaded ({len(equity_universe_df_local):,} EQ rows). "
            f"Now walking up to {int(feeder_pool_size):,} tickers to find up to "
            f"{int(max_stocks)} in the {size_choice} band…"
        )
        walk_df = equity_universe_df_local.head(int(feeder_pool_size)).copy()

        # Fast path: use MCap_Cr from fundamentals_master if available
        if (
            fundamentals_master_df is not None
            and not fundamentals_master_df.empty
            and "MCap_Cr" in fundamentals_master_df.columns
        ):
            mc_df  = fundamentals_master_df[["Ticker", "MCap_Cr"]].copy()
            mc_df["Ticker"] = mc_df["Ticker"].astype(str).str.upper()
            merged = walk_df.merge(mc_df, on="Ticker", how="left")
            in_band = merged[
                merged["MCap_Cr"].notna()
                & (merged["MCap_Cr"] >= mcap_min_cr)
                & (merged["MCap_Cr"] <= mcap_max_cr)
            ].head(int(max_stocks))
            tickers_in_band = in_band["Ticker"].tolist()
            st.info(
                f"MCap filter (fast path via fundamentals_master): "
                f"{len(tickers_in_band)} ticker(s) in band from {len(merged)} checked."
            )
        else:
            st.warning(
                "No MCap_Cr column in fundamentals_master.csv — "
                "using live Yahoo Finance for MCap filter (slower)."
            )
            filtered_df, checked_count = apply_mcap_filter(
                walk_df, mcap_min_cr, mcap_max_cr, int(max_stocks), pause_between_calls
            )
            tickers_in_band = filtered_df["Ticker"].tolist()
            st.info(
                f"MCap filter (live): checked {checked_count}, "
                f"found {len(tickers_in_band)} in band."
            )

        tickers_to_screen = [f"{t}.NS" for t in tickers_in_band]
        st.info(
            f"Final live-screen universe: {len(tickers_to_screen)} stock(s) "
            f"in {size_choice} band (₹{mcap_min_cr:,.0f}–₹{mcap_max_cr:,.0f} Cr)."
        )
    else:
        tickers_to_screen = DEFAULT_UNIVERSE
        st.warning("No NSE bhavcopy uploaded — falling back to DEFAULT_UNIVERSE (6 stocks).")

    if not tickers_to_screen:
        st.error(
            "Zero stocks found in the selected MCap band. "
            "Try a wider band or upload a complete bhavcopy file."
        )
        st.stop()

    # ── 5. Auto-expand fundamentals & build stockmaster ───
    target_base_tickers = [
        t.replace(".NS", "").upper().strip() for t in tickers_to_screen
    ]

    fundamentals_master_df = auto_expand_fundamentals(
        fundamentals_master_df, target_base_tickers
    )

    with st.spinner("Building stockmaster (company / sector / subsector) via yfinance…"):
        stock_master_df = build_stockmaster_from_tickers(
            target_base_tickers, pause_between_calls=pause_between_calls
        )
    st.info(f"Stockmaster built: {len(stock_master_df)} rows.")

    # Rebuild shareholding lookup now that we have stockmaster (improves matching)
    if uploaded_sh_file is not None:
        try:
            uploaded_sh_file.seek(0)
            sh_raw_df = pd.read_csv(uploaded_sh_file)
            sh_raw_df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in sh_raw_df.columns]
            shareholding_lookup = build_shareholding_lookup(sh_raw_df, stock_master_df)
            st.info(f"Shareholding lookup rebuilt with stockmaster: {len(shareholding_lookup)} ticker(s) matched.")
        except Exception as e:
            st.warning(f"Could not rebuild shareholding lookup with stockmaster: {e}")

    rebuild_fundamentals_lookup(fundamentals_master_df)

    missing_in_fundamentals = [
        t for t in target_base_tickers
        if t not in fundamentals_lookup
    ]
    if missing_in_fundamentals:
        st.info(
            f"{len(missing_in_fundamentals)} ticker(s) not in uploaded fundamentals — "
            f"yfinance data will be used for those: {', '.join(missing_in_fundamentals[:10])}"
            + (" …" if len(missing_in_fundamentals) > 10 else "")
        )

    st.write(f"Found {len(tickers_to_screen)} stocks to screen.")

    # ── 6. Evaluate each stock ────────────────────────────
    rows: List[Dict[str, Any]] = []
    progress_bar = st.progress(0)
    status_text  = st.empty()
    total        = len(tickers_to_screen)

    with st.spinner("Fetching live data from Yahoo Finance…"):
        for i, ticker in enumerate(tickers_to_screen):
            status_text.text(f"Screening {ticker} ({i + 1}/{total})…")
            row = evaluate_stock(ticker, weights)
            rows.append(row)
            progress_bar.progress((i + 1) / total)
            time.sleep(pause_between_calls)

    status_text.empty()
    progress_bar.empty()

    df             = pd.DataFrame(rows)
    screened_count = len(df)

    # ── 7. Enrich with stockmaster ────────────────────────
    if not df.empty and stock_master_df is not None and not stock_master_df.empty:
        merge_cols = [c for c in ["Ticker", "Sector", "SubSector"]
                      if c in stock_master_df.columns]
        if len(merge_cols) > 1:
            df = df.merge(
                stock_master_df[merge_cols], on="Ticker",
                how="left", suffixes=("", "_stock")
            )
            if "Sector_stock" in df.columns:
                df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
                df.drop(columns=["Sector_stock"], inplace=True)
            if "SubSector_stock" in df.columns:
                df.rename(columns={"SubSector_stock": "SubSector"}, inplace=True)

    # ── 8. Enrich with fundamentals_master columns ────────
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
            fundamentals_master_df[fund_merge_cols], on="Ticker",
            how="left", suffixes=("", "_fund")
        )

    summary_df_before_pass_filter = df.copy()

    # ── 9. Apply display filters ──────────────────────────
    if only_pass:
        if show_datagap:
            df = df[df["ScreenVerdict"].isin([VERDICT_PASS, VERDICT_PASS_DATAGAP])].copy()
        else:
            df = df[df["ScreenVerdict"] == VERDICT_PASS].copy()

    if min_score > 0:
        df = df[df["Conviction"] >= min_score].copy()

    # Sort
    if not df.empty:
        verdict_order = {
            VERDICT_PASS: 0, VERDICT_PASS_DATAGAP: 1,
            VERDICT_FAIL_GENUINE: 2, VERDICT_FAIL_NODATA: 3,
        }
        df["_vsort"] = df["ScreenVerdict"].map(verdict_order).fillna(9).astype(int)
        df = df.sort_values(
            ["_vsort", "WeightedScore", "Conviction", "L5_Score_0to1", "L3_Score_0to1"],
            ascending=[True, False, False, False, False],
        )
        df.drop(columns=["_vsort"], inplace=True)

    # ── 10. Metrics ───────────────────────────────────────
    n_pass    = int((summary_df_before_pass_filter["ScreenVerdict"] == VERDICT_PASS).sum())
    n_datagap = int((summary_df_before_pass_filter["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum())
    n_genuine = int((summary_df_before_pass_filter["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum())
    n_nodata  = int((summary_df_before_pass_filter["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum())

    st.success(
        f"Screen complete — {screened_count} screened, {len(df)} stock(s) shown "
        f"| Band: {size_choice}"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ PASS",              n_pass)
    c2.metric("⚠️ PASS (Data gaps)", n_datagap)
    c3.metric("❌ FAIL (Genuine)",   n_genuine)
    c4.metric("🔲 FAIL (No data)",   n_nodata)

    # MCap band breakdown
    band_breakdown = (
        summary_df_before_pass_filter
        .groupby("MCap_Band")["Ticker"]
        .count().reset_index()
        .rename(columns={"Ticker": "Count"})
    )
    if not band_breakdown.empty:
        st.subheader("MCap band breakdown of screened universe")
        st.dataframe(band_breakdown, use_container_width=True)

    # Failure reason summary
    failure_reason_summary = build_failure_reason_summary(summary_df_before_pass_filter)
    if not failure_reason_summary.empty:
        st.subheader("Failure reason summary")
        st.dataframe(failure_reason_summary, use_container_width=True)

    # Failed names detail
    fail_detail = summary_df_before_pass_filter[
        summary_df_before_pass_filter["ScreenVerdict"] == VERDICT_FAIL_GENUINE
    ][[
        "Ticker", "MCap_Cr", "MCap_Band", "Conviction", "ConvictionPct",
        "WeightedScore", "FailReasons", "DataGapReasons",
        "L3_Score_0to1", "L5_Score_0to1",
    ]].copy()
    if not fail_detail.empty:
        st.subheader("Failed names summary")
        st.dataframe(
            fail_detail.sort_values(
                ["WeightedScore", "Conviction"], ascending=[False, False]
            ),
            use_container_width=True,
        )

    # Pass results
    if not df.empty:
        st.subheader("✅ Pass results")
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "⬇️ Download pass results CSV",
            data=df.to_csv(index=False),
            file_name="100x_screener_v7_results.csv",
            mime="text/csv",
        )
        st.download_button(
            "⬇️ Download full screen CSV (all stocks)",
            data=summary_df_before_pass_filter.to_csv(index=False),
            file_name="100x_screener_v7_full.csv",
            mime="text/csv",
        )
    else:
        st.info(
            "No stocks passed the current filters. "
            "Try relaxing conviction score or widening the MCap band."
        )

else:
    st.info(
        "👆 Upload files in the sidebar, choose your cap size band, "
        "then click **Run live screen**."
    )

# ─── Static reference table ────────────────────────────────
st.subheader("Cap size classification reference")
ref_data = {
    "Band": ["🔬 Nano only", "🌱 Small cap only", "🔬+🌱 Nano + Small", "📐 Extended"],
    "MCap Range": ["< ₹500 Cr", "₹500 – ₹5,000 Cr", "< ₹5,000 Cr", "< ₹20,000 Cr"],
    "Why it fits your mandate": [
        "Highest analyst neglect → biggest mispricing window",
        "SEBI-defined small cap; reasonable liquidity; 10x+ potential",
        "Recommended default — widest quality-value hunting ground",
        "Extended net — catches temporarily cheap quality mid-caps",
    ],
}
st.dataframe(pd.DataFrame(ref_data), use_container_width=True)
