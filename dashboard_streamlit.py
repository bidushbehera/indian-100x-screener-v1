import re
import time
import difflib
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="100X Screener V1 - Indian Equities", layout="wide")
st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "Attachment-driven screener using NSE bhavcopy + NSE shareholding CSV + "
    "optional uploaded master files. Manual overrides are only a last-resort exception layer."
)

# =========================================================
# CONFIG
# =========================================================
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
    "promoter_min":    40.0,
    "insider_min":      0.40,
    "quality_min_raw":  5,
    "name_match_cutoff": 0.88,
}

VERDICT_PASS         = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA  = "FAIL (Insufficient data)"

# =========================================================
# LAST-RESORT EXCEPTION LAYER ONLY
# Keep this tiny. This is NOT the primary mapping source.
# =========================================================
MANUAL_NAME_OVERRIDES: Dict[str, str] = {
    # "HINDZINC": "Hindustan Zinc Limited",
}

fundamentals_lookup: Dict[str, Any] = {}
symbol_company_master: Dict[str, str] = {}

# =========================================================
# HELPERS
# =========================================================
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
    remeToCommon") or 0
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


def normalize_company_name(name: str) -> str:
    if name is None:
        return ""
    s = str(name).strip().lower()
    s = s.replace("\ufeff", "").replace('"', "").replace("'", "")
    s = s.replace("&", " and ")
    s = s.replace(" co.", " company ")
    s = s.replace(" co ", " company ")
    s = s.replace("(india)", " india ")
    s = s.replace(" india ltd", " india limited")
    s = s.replace(" ltd.", " limited")
    s = s.replace(" ltd", " limited")
    s = s.replace(" pvt.", " private")
    s = s.replace(" pvt ", " private ")
    s = s.replace(" priv ", " private ")
    s = s.replace(" technologies", " technology")
    s = s.replace(" solutions", " solution")
    s = s.replace(" services", " service")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    tokens_to_drop = {
        "limited", "ltd", "company", "co", "private", "public",
        "india", "industries", "industry", "corporation", "corp"
    }
    parts = [p for p in s.split() if p not in tokens_to_drop]
    return " ".join(parts).strip()


def score_name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_company_name(a), normalize_company_name(b)).ratio()


def load_fundamentals_master() -> pd.DataFrame:
    try:
        return pd.read_csv("fundamentals_master.csv")
    except Exception:
        return pd.DataFrame()


def load_stock_master() -> pd.DataFrame:
    try:
        return pd.read_csv("stock_master.csv")
    except Exception:
        return pd.DataFrame()


def rebuild_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> None:
    global fundamentals_lookup
    fundamentals_lookup = {}
    if fundamentals_master_df is None or fundamentals_master_df.empty:
        return
    tmp = fundamentals_master_df.copy()
    if "Ticker" not in tmp.columns:
        return
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper().str.strip()
    fundamentals_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def detect_company_name_column(df: pd.DataFrame) -> Optional[str]:
    cols = [c.strip().lstrip("\ufeff").strip('"') for c in df.columns]
    df.columns = cols
    preferred = ["CompanyName", "Company", "COMPANY", "Issuer Name", "NAME OF COMPANY", "Security Name"]
    for col in preferred:
        if col in cols:
            return col
    return None


def detect_symbol_column(df: pd.DataFrame) -> Optional[str]:
    cols = [c.strip().lstrip("\ufeff").strip('"') for c in df.columns]
    df.columns = cols
    preferred = ["Ticker", "Symbol", "SYMBOL", "TckrSymb", "Security Code", "Code"]
    for col in preferred:
        if col in cols:
            return col
    return None


def build_symbol_company_master(
    stock_master_df: pd.DataFrame,
    bhavcopy_df: pd.DataFrame,
    fundamentals_df: pd.DataFrame
) -> Tuple[Dict[str, str], pd.DataFrame]:
    mapping_rows = []

    def ingest(df: pd.DataFrame, source_name: str):
        if df is None or df.empty:
            return
        tmp = df.copy()
        symbol_col = detect_symbol_column(tmp)
        company_col = detect_company_name_column(tmp)
        if symbol_col and company_col:
            sub = tmp[[symbol_col, company_col]].copy()
            sub.columns = ["Ticker", "CompanyName"]
            sub["Ticker"] = sub["Ticker"].astype(str).str.upper().str.strip()
            sub["CompanyName"] = sub["CompanyName"].astype(str).str.strip()
            sub["Source"] = source_name
            mapping_rows.append(sub)

    ingest(stock_master_df, "stock_master")
    ingest(fundamentals_df, "fundamentals_master")
    ingest(bhavcopy_df, "bhavcopy")

    if not mapping_rows:
        return {}, pd.DataFrame()

    master = pd.concat(mapping_rows, ignore_index=True)
    master = master.dropna(subset=["Ticker", "CompanyName"])
    master = master[(master["Ticker"] != "") & (master["CompanyName"] != "")]
    master = master.drop_duplicates(subset=["Ticker", "CompanyName"])

    source_rank = {"stock_master": 1, "fundamentals_master": 2, "bhavcopy": 3}
    master["rank"] = master["Source"].map(source_rank).fillna(99)
    master = master.sort_values(["Ticker", "rank", "CompanyName"])
    best = master.drop_duplicates(subset=["Ticker"], keep="first").copy()

    mapping = dict(zip(best["Ticker"], best["CompanyName"]))
    return mapping, best.drop(columns=["rank"])


def build_shareholding_company_index(shareholding_df: pd.DataFrame) -> pd.DataFrame:
    df = shareholding_df.copy()
    df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in df.columns]

    required = [
        "COMPANY",
        "PROMOTER & PROMOTER GROUP (A)",
        "PUBLIC (B)",
        "SHARES HELD BY EMPLOYEE TRUSTS (C2)",
        "STATUS",
        "AS ON DATE",
        "REVISION DATE",
        "ACTION",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Shareholding CSV missing required columns: {missing}")
        return pd.DataFrame()

    df["CompanyNameRaw"] = df["COMPANY"].astype(str).str.strip()
    df["CompanyNameNorm"] = df["CompanyNameRaw"].apply(normalize_company_name)
    return df


def resolve_symbol_to_shareholding(
    ticker: str,
    company_master_name: Optional[str],
    shareholding_company_df: pd.DataFrame,
    manual_overrides: Dict[str, str],
) -> Dict[str, Any]:
    ticker = str(ticker).upper().strip()

    if ticker in manual_overrides:
        target = manual_overrides[ticker]
        exact = shareholding_company_df[
            shareholding_company_df["CompanyNameRaw"].str.lower() == str(target).lower()
        ]
        if not exact.empty:
            row = exact.iloc[0]
            return {
                "ResolvedCompanyName": row["CompanyNameRaw"],
                "MappingMethod": "manual_override",
                "MatchScore": 1.0,
                "Row": row,
            }

    if company_master_name and isinstance(company_master_name, str) and company_master_name.strip():
        raw = company_master_name.strip()

        exact = shareholding_company_df[
            shareholding_company_df["CompanyNameRaw"].str.lower() == raw.lower()
        ]
        if not exact.empty:
            row = exact.iloc[0]
            return {
                "ResolvedCompanyName": row["CompanyNameRaw"],
                "MappingMethod": "exact_name",
                "MatchScore": 1.0,
                "Row": row,
            }

        norm = normalize_company_name(raw)
        norm_match = shareholding_company_df[shareholding_company_df["CompanyNameNorm"] == norm]
        if not norm_match.empty:
            row = norm_match.iloc[0]
            return {
                "ResolvedCompanyName": row["CompanyNameRaw"],
                "MappingMethod": "normalized_exact",
                "MatchScore": 0.99,
                "Row": row,
            }

        candidates = shareholding_company_df[["CompanyNameRaw", "CompanyNameNorm"]].drop_duplicates().copy()
        candidates["score"] = candidates["CompanyNameRaw"].apply(lambda x: score_name_similarity(raw, x))
        candidates = candidates.sort_values("score", ascending=False)

        if not candidates.empty and float(candidates.iloc[0]["score"]) >= CONFIG["name_match_cutoff"]:
            best_name = candidates.iloc[0]["CompanyNameRaw"]
            row = shareholding_company_df[shareholding_company_df["CompanyNameRaw"] == best_name].iloc[0]
            return {
                "ResolvedCompanyName": row["CompanyNameRaw"],
                "MappingMethod": "fuzzy",
                "MatchScore": float(candidates.iloc[0]["score"]),
                "Row": row,
            }

    return {
        "ResolvedCompanyName": None,
        "MappingMethod": "unmapped",
        "MatchScore": None,
        "Row": None,
    }


def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()
    df = nse_df.copy()
    df.columns = [c.strip().lstrip("\ufeff").strip('"') for c in df.columns]
    required_cols = ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"NSE bhavcopy CSV is missing required column: {col}")
            return pd.DataFrame()
    df = df[df["FinInstrmTp"] == "STK"]
    df = df[df["SctySrs"] == "EQ"]
    if df.empty:
        return pd.DataFrame()
    keep_cols = ["TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()
    df = df.rename(columns={
        "TckrSymb": "Ticker",
        "SctySrs": "Series",
        "ClsPric": "Close",
        "TtlTradgVol": "Volume",
        "TtlTrfVal": "Turnover",
    })
    df["Ticker"] = df["Ticker"].astype(str).str.upper().str.strip()
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def compute_screen_verdict(
    l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
    l1_data_missing, l2_data_missing, l3_data_missing,
    l4_data_missing, l5_data_missing,
) -> str:
    layers_missing = [l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing]
    layers_pass    = [l1_val, l2_prof, l3_cf, l4_share, l5_forensic]
    testable_count = sum(1 for m in layers_missing if not m)

    if testable_count < 3:
        return VERDICT_FAIL_NODATA

    genuine_failure = any((not passed) and (not missing) for passed, missing in zip(layers_pass, layers_missing))
    if genuine_failure:
        return VERDICT_FAIL_GENUINE

    if any(layers_missing):
        return VERDICT_PASS_DATAGAP

    return VERDICT_PASS


# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.header("Controls")
min_score = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)
show_datagap = st.sidebar.checkbox("Also show PASS (Data gaps present)", value=True)
max_stocks = st.sidebar.number_input("Max stocks to screen (top by NSE turnover)", 10, 500, 50, 10)

st.sidebar.markdown("---")
uploaded_nse_file = st.sidebar.file_uploader(
    "Upload NSE bhavcopy CSV",
    type=["csv"],
    key="nse_bhavcopy_upload_refactor"
)

uploaded_sh_file = st.sidebar.file_uploader(
    "Upload NSE shareholding CSV",
    type=["csv"],
    key="nse_shareholding_upload_refactor"
)

pause_between_calls = st.sidebar.slider("Pause between API calls (seconds)", 0.0, 1.0, 0.2, 0.1)

with st.expander("How mapping works", expanded=False):
    st.markdown("""
**Resolution order**
1. Auto symbol-company master from uploaded files
2. Exact name match into shareholding CSV
3. Normalized-name match
4. Fuzzy match above confidence cutoff
5. Small manual override table only if needed
6. Otherwise mark stock as **Unmapped**
""")

# =========================================================
# LOAD LOCAL MASTERS
# =========================================================
fundamentals_df = load_fundamentals_master()
stock_master_df = load_stock_master()
rebuild_fundamentals_lookup(fundamentals_df)

# =========================================================
# PREVIEWS
# =========================================================
st.subheader("Uploaded data status")

col_a, col_b, col_c = st.columns(3)
col_a.metric("fundamentals_master rows", 0 if fundamentals_df.empty else len(fundamentals_df))
col_b.metric("stock_master rows", 0 if stock_master_df.empty else len(stock_master_df))
col_c.metric("manual overrides", len(MANUAL_NAME_OVERRIDES))

shareholding_company_df = pd.DataFrame()
if uploaded_sh_file is not None:
    try:
        uploaded_sh_file.seek(0)
        sh_raw_df = pd.read_csv(uploaded_sh_file)
        shareholding_company_df = build_shareholding_company_index(sh_raw_df)
        st.success(f"Shareholding CSV loaded: {len(shareholding_company_df)} rows")
    except Exception as e:
        st.error(f"Error reading shareholding CSV: {e}")

bhavcopy_equity_df = pd.DataFrame()
bhavcopy_raw_df = pd.DataFrame()
if uploaded_nse_file is not None:
    try:
        uploaded_nse_file.seek(0)
        bhavcopy_raw_df = pd.read_csv(uploaded_nse_file)
        bhavcopy_equity_df = build_nse_equity_universe(bhavcopy_raw_df)
        if not bhavcopy_equity_df.empty:
            st.success(f"Bhavcopy loaded: {len(bhavcopy_equity_df)} EQ stocks")
    except Exception as e:
        st.error(f"Error reading bhavcopy CSV: {e}")

symbol_company_master, symbol_company_master_df = build_symbol_company_master(
    stock_master_df=stock_master_df,
    bhavcopy_df=bhavcopy_raw_df,
    fundamentals_df=fundamentals_df
)

with st.expander("Symbol-company master preview", expanded=False):
    if symbol_company_master_df.empty:
        st.warning(
            "No auto symbol-company master could be built from uploaded/local files. "
            "Mapping can still work only through manual overrides, which is not ideal."
        )
    else:
        st.write(f"Auto symbol-company master built for {len(symbol_company_master_df)} symbols")
        st.dataframe(symbol_company_master_df.head(50), use_container_width=True)

# =========================================================
# MAIN EVALUATION
# =========================================================
def evaluate_stock(ticker: str, shareholding_csv_uploaded: bool) -> Dict[str, Any]:
    try:
        base_ticker = ticker.replace(".NS", "").upper().strip()
        yf_ticker   = yf.Ticker(ticker)
        info        = yf_ticker.info
        fund_row    = fundamentals_lookup.get(base_ticker)

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
        current_liab = safe(turn num / 100.0 if num > 1.5 else num


def approx_quality_score(info: Dict[str, Any]) -> int:
    score = 0
    ni  = safe(info, "netIncorrent_liab is not None:
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
                        if var_name == "roe": roe = v
                        elif var_name == "roce": roce = v
                        elif var_name == "opm": opm = v
                        elif var_name == "revg": revg = v
                        elif var_name == "earng": earng = v

        master_company_name = symbol_company_master.get(base_ticker)

        resolved_company_name = None
        mapping_method = None
        mapping_score = None
        promoter_pct_nse = None
        public_pct_nse = None
        emp_pct_nse = None
        ownership_total_pct = None
        ownership_valid = False
        has_shareholding_data = False
        shareholding_status = "Not available"
        sh_as_on_date = None
        sh_revision_date = None
        sh_action_link = None

        if shareholding_csv_uploaded and not shareholding_company_df.empty:
            resolution = resolve_symbol_to_shareholding(
                ticker=base_ticker,
                company_master_name=master_company_name,
                shareholding_company_df=shareholding_company_df,
                manual_overrides=MANUAL_NAME_OVERRIDES,
            )
            resolved_company_name = resolution["ResolvedCompanyName"]
            mapping_method = resolution["MappingMethod"]
            mapping_score = resolution["MatchScore"]
            row = resolution["Row"]

            if row is not None:
                promoter_pct_nse = pd.to_numeric(row["PROMOTER & PROMOTER GROUP (A)"], errors="coerce")
                public_pct_nse   = pd.to_numeric(row["PUBLIC (B)"], errors="coerce")
                emp_pct_nse      = pd.to_numeric(row["SHARES HELD BY EMPLOYEE TRUSTS (C2)"], errors="coerce")
                sh_as_on_date    = row["AS ON DATE"]
                sh_revision_date = row["REVISION DATE"]
                sh_action_link   = row["ACTION"]
                parts = [x for x in [promoter_pct_nse, public_pct_nse, emp_pct_nse] if pd.notna(x)]
                ownership_total_pct = round(sum(parts), 2) if parts else None
                ownership_valid = (
                    promoter_pct_nse is not None and pd.notna(promoter_pct_nse)
                    and public_pct_nse is not None and pd.notna(public_pct_nse)
                    and ownership_total_pct is not None
                    and abs(ownership_total_pct - 100.0) < 5.0
                )
                has_shareholding_data = True
                shareholding_status = f"NSE CSV ({mapping_method})"
            else:
                shareholding_status = "Unmapped"

        if shareholding_csv_uploaded:
            if has_shareholding_data:
                l4_share = bool(ownership_valid and promoter_pct_nse >= CONFIG["promoter_min"])
                l4_data_missing = False
            else:
                l4_share = False
                l4_data_missing = True
        else:
            l4_share = bool(insider is not None and insider > CONFIG["insider_min"])
            l4_data_missing = insider is None
            if insider is not None:
                shareholding_status = "yfinance (fallback)"

        l1_checks = [
            pe is not None and pe < CONFIG["pe_max"],
            peg is not None and peg < CONFIG["peg_max"],
            ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
            pb is not None and pb < CONFIG["pb_max"],
            mcap_cr is not None and CONFIG["mcap_min_cr"] <= mcap_cr <= CONFIG["mcap_max_cr"],
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
        l5_data_missing = l5_fields_preseturn num / 100.0 if num > 1.5 else num


def approx_quality_score(info: Dict[str, Any]) -> int:
    score = 0
    ni  = safe(info, "netIncoviction >= 4)

        verdict = compute_screen_verdict(
            l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
            l1_data_missing, l2_data_missing, l3_data_missing,
            l4_data_missing, l5_data_missing,
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
        qp = round(10 * quality_raw / 7) if quality_raw is not None else 0
        ws += min(qp, 10)

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
            "YahooInsider_pct": round(insider * 100, 2) if insider is not None else None,
            "ResolvedCompanyName": resolved_company_name,
            "MappingMethod": mapping_method,
            "MappingScore": mapping_score,
            "PromoterPct_NSE": round(float(promoter_pct_nse), 2) if promoter_pct_nse is not None and pd.notna(promoter_pct_nse) else None,
            "PublicPct_NSE": round(float(public_pct_nse), 2) if public_pct_nse is not None and pd.notna(public_pct_nse) else None,
            "EmployeeTrustPct_NSE": round(float(emp_pct_nse), 2) if emp_pct_nse is not None and pd.notna(emp_pct_nse) else None,
            "OwnershipTotalPct": ownership_total_pct,
            "OwnershipDataValid": ownership_valid,
            "ShareholdingStatus": shareholding_status,
            "ShareholdingAsOnDate": sh_as_on_date,
            "ShareholdingRevisionDate": sh_revision_date,
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
            "ScreenVerdict": verdict,
            "HasFundamentals": fund_row is not None,
            "HasShareholdingData": has_shareholding_data,
            "Error": None,
            "ShareholdingActionLink": sh_action_link,
        }

    except Exception as e:
        base_ticker = ticker.replace(".NS", "").upper().strip()
        return {
            "Ticker": base_ticker,
            "ScreenVerdict": VERDICT_FAIL_NODATA,
            "Conviction": 0,
            "WeightedScore": 0,
            "Pass": False,
            "HasFundamentals": False,
            "HasShareholdingData": False,
            "ShareholdingStatus": "Error",
            "Error": str(e),
        }


# =========================================================
# RUN
# =========================================================
if st.button("Run live screen"):
    shareholding_csv_uploaded = uploaded_sh_file is not None and not shareholding_company_df.empty

    if not bhavcopy_equity_df.empty:
        universe = bhavcopy_equity_df.head(int(max_stocks))
        tickers_to_screen = [f"{t}.NS" for t in universe["Ticker"].tolist()]
        st.info(f"Using NSE bhavcopy universe: screening top {len(tickers_to_screen)} stock(s) by turnoveturn num / 100.0 if num > 1.5 else num


def approx_quality_score(info: Dict[str, Any]) -> int:
    score = 0
    ni  = safe(info, "netInco.")

    st.write(f"Found {len(tickers_to_screen)} stocks to screen")

    rows = []
    progress_bar = st.progress(0)
    total_tickers = len(tickers_to_screen)

    with st.spinner("Fetching live data..."):
        for i, ticker in enumerate(tickers_to_screen):
            rows.append(evaluate_stock(ticker, shareholding_csv_uploaded=shareholding_csv_uploaded))
            progress_bar.progress((i + 1) / total_tickers)
            time.sleep(pause_between_calls)

    progress_bar.empty()

    df = pd.DataFrame(rows)

    if not df.empty and stock_master_df is not None and not stock_master_df.empty and "Ticker" in stock_master_df.columns:
        merge_cols = [c for c in ["Ticker", "Sector", "SubSector"] if c in stock_master_df.columns]
        if len(merge_cols) > 1:
            df = df.merge(stock_master_df[merge_cols], on="Ticker", how="left", suffixes=("", "_stock"))
            if "Sector_stock" in df.columns:
                df["Sector"] = df["Sector_stock"].combine_first(df.get("Sector"))
                df.drop(columns=["Sector_stock"], inplace=True)

    if not df.empty and fundamentals_df is not None and not fundamentals_df.empty and "Ticker" in fundamentals_df.columns:
        fundamentals_cols = [
            "Ticker", "Latest_Year",
            "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
            "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
            "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
            "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag", "Reg_Risk_Flag", "Gov_Risk_Flag",
        ]
        fundamentals_cols = [c for c in fundamentals_cols if c in fundamentals_df.columns]
        df = df.merge(fundamentals_df[fundamentals_cols], on="Ticker", how="left")

    if not df.empty:
        mapped = df["ResolvedCompanyName"].notna().sum() if "ResolvedCompanyName" in df.columns else 0
        unmapped = (df["ShareholdingStatus"] == "Unmapped").sum() if "ShareholdingStatus" in df.columns else 0
        st.write(f"Mapping summary: mapped {mapped}, unmapped {unmapped}")

        unresolved = df[df["ShareholdingStatus"] == "Unmapped"][["Ticker", "ResolvedCompanyName", "MappingMethod"]].copy() \
            if "ShareholdingStatus" in df.columns else pd.DataFrame()
        if not unresolved.empty:
            with st.expander("Unmapped stocks requiring exception review", expanded=False):
                st.dataframe(unresolved, use_container_width=True)

    if only_pass:
        if show_datagap:
            df = df[df["ScreenVerdict"].isin([VERDICT_PASS, VERDICT_PASS_DATAGAP])]
        else:
            df = df[df["ScreenVerdict"] == VERDICT_PASS]

    if min_score > 0:
        df = df[df["Conviction"] >= min_score]

    verdict_order = {
        VERDICT_PASS: 0,
        VERDICT_PASS_DATAGAP: 1,
        VERDICT_FAIL_GENUINE: 2,
        VERDICT_FAIL_NODATA: 3,
    }
    if not df.empty:
        df["_vsort"] = df["ScreenVerdict"].map(verdict_order).fillna(9).astype(int)
        df = df.sort_values(["_vsort", "WeightedScore", "Conviction"], ascending=[True, False, False]).drop(columns=["_vsort"])

    total = len(df)
    n_pass = (df["ScreenVerdict"] == VERDICT_PASS).sum() if not df.empty else 0
    n_datagap = (df["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum() if not df.empty else 0
    n_genuine = (df["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum() if not df.empty else 0
    n_nodata = (df["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum() if not df.empty else 0

    st.success(f"Screen complete — {total} stock(s) shown")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PASS", n_pass)
    c2.metric("PASS (Data gaps)", n_datagap)
    c3.metric("FAIL (Genuine)", n_genuine)
    c4.metric("FAIL (No data)", n_nodata)

    if not df.empty:
        preferred_order = [
            "Ticker", "Sector", "SubSector", "ScreenVerdict", "Price", "MCap_Cr",
            "PE", "PB", "PEG", "ROCE_pct", "ROE_pct", "ROA_pct", "OPM_pct",
            "RevGrowth_pct", "EarnGrowth_pct", "OCF_PAT", "FCFYield_pct",
            "ResolvedCompanyName", "MappingMethod", "MappingScore",
            "PromoterPct_NSE", "PublicPct_NSE", "EmployeeTrustPct_NSE",
            "OwnershipTotalPct", "OwnershipDataValid", "ShareholdingStatus",
            "ShareholdingAsOnDate", "ShareholdingRevisionDate",
            "QualityScore_raw", "L1_Val", "L2_Prof", "L3_CF", "L4_Share", "L5_Forensic",
            "Conviction", "WeightedScore", "Pass",
            "HasFundamentals", "HasShareholdingData",
            "Latest_Year", "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
            "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears",
            "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
            "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag", "Reg_Risk_Flag", "Gov_Risk_Flag",
            "Error", "ShareholdingActionLink",
        ]
        existing_cols = [c for c in preferred_order if c in df.columns]
        remaining_cols = [c for c in df.columns if c not in existing_cols]
        df = df[existing_cols + remaining_cols]

        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download CSV",
            data=df.to_csv(index=False),
            file_name="100x_screener_refactored_results.csv",
            mime="text/csv",
        )
    else:
        st.info("No stocks passed the current filters.")
else:
    st.info("Click **Run live screen** to start.")
