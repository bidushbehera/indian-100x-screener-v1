import time
import re
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf


st.set_page_config(page_title="100X Screener V1 - Indian Equities", layout="wide")
st.title("100X Screener V1 — Indian Equity Live Screener")
st.caption(
    "Single-page Streamlit screener using Yahoo Finance, fundamentals_master.csv, "
    "and optional NSE shareholding CSV for stronger L4 ownership scoring."
)

# -----------------------------
# HARD-CODED MAPS / OVERRIDES
# -----------------------------
TICKER_TO_COMPANY: Dict[str, str] = {
    "POLYCAB": "Polycab India Limited",
    "TANLA": "Tanla Platforms Limited",
    "KPITTECH": "KPIT Technologies Limited",
    "CDSL": "Central Depository Services (India) Limited",
    "CAMS": "Computer Age Management Services Limited",
    "IRCTC": "Indian Railway Catering And Tourism Corporation Limited",
    "CGPOWER": "CG Power and Industrial Solutions Limited",
    "DEEPAKNTR": "Deepak Nitrite Limited",
    "OLECTRA": "Olectra Greentech Limited",
    "LLOYDSME": "Lloyds Metals And Energy Limited",
    "HINDZINC": "Hindustan Zinc Limited",
}

# Optional last-resort ownership overrides for known names when parser misses a row.
# Keep this tiny and explicit.
MANUAL_SHAREHOLDING_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "TANLA": {
        "PromoterPct_NSE": 46.17,
        "PublicPct_NSE": 53.36,
        "EmployeeTrustPct_NSE": 0.47,
        "ShareholdingAsOnDate": "31-MAR-2026",
        "Source": "Manual override",
    },
    "HINDZINC": {
        "PromoterPct_NSE": 60.71,
        "PublicPct_NSE": 39.29,
        "EmployeeTrustPct_NSE": 0.00,
        "ShareholdingAsOnDate": "31-MAR-2026",
        "Source": "Manual override",
    },
}

DEFAULT_UNIVERSE: List[str] = [
    "LLOYDSME.NS", "POLYCAB.NS", "DEEPAKNTR.NS", "CGPOWER.NS", "TANLA.NS",
    "KPITTECH.NS", "CDSL.NS", "CAMS.NS", "IRCTC.NS", "OLECTRA.NS",
]

CONFIG: Dict[str, Any] = {
    "pe_max": 20.0,
    "peg_max": 1.0,
    "ev_ebitda_max": 12.0,
    "pb_max": 3.0,
    "mcap_min_cr": 200.0,
    "mcap_max_cr": 5000.0,
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
}

VERDICT_PASS = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA = "FAIL (Insufficient data)"

fundamentals_lookup: Dict[str, pd.Series] = {}
shareholding_lookup: Dict[str, Dict[str, Any]] = {}

st.sidebar.caption(f"yfinance version: {yf.__version__}")


def safe(info: Dict[str, Any], key: str, default=None):
    value = info.get(key, default)
    if value in (None, "N/A", "NaN"):
        return default
    return value


def parse_percent_or_float(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if not text:
                return None
            if text.endswith("%"):
                text = text[:-1].strip()
            num = float(text)
        else:
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


def load_csv_if_exists(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def rebuild_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> None:
    global fundamentals_lookup
    fundamentals_lookup = {}
    if fundamentals_master_df.empty or "Ticker" not in fundamentals_master_df.columns:
        return
    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper().str.strip()
    fundamentals_lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}


def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()
    required = ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    missing = [c for c in required if c not in nse_df.columns]
    if missing:
        st.error(f"NSE price CSV missing required columns: {missing}")
        return pd.DataFrame()
    df = nse_df.copy()
    df = df[(df["FinInstrmTp"] == "STK") & (df["SctySrs"] == "EQ")]
    if df.empty:
        return pd.DataFrame()
    df = df[["TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol", "TtlTrfVal"]].rename(
        columns={
            "TckrSymb": "Ticker",
            "SctySrs": "Series",
            "ClsPric": "Close",
            "TtlTradgVol": "Volume",
            "TtlTrfVal": "Turnover",
        }
    )
    df["Ticker"] = df["Ticker"].astype(str).str.upper().str.strip()
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def normalize_company_name(name: str) -> str:
    if name is None:
        return ""
    s = str(name).upper().strip()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    replacements = {
        " LIMITED": " LTD",
        " LIMITED ": " LTD ",
        " AND ": " ",
        " CORPORATION": " CORP",
        " SERVICES": " SERVICE",
        " TECHNOLOGIES": " TECHNOLOGY",
        " SOLUTIONS": " SOLUTION",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    return re.sub(r"\s+", " ", s).strip()


def parse_shareholding_text(content: str) -> pd.DataFrame:
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    rows = []
    company_to_ticker = {normalize_company_name(v): k for k, v in TICKER_TO_COMPANY.items()}

    patterns = [
        re.compile(r"^(.*?)\s*(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)(\d*(?:\.\d+)?)\s*-?(31-[A-Z]{3}-\d{4})"),
        re.compile(r"^(.*?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d*(?:\.\d+)?)\s*-?(31-[A-Z]{3}-\d{4})"),
    ]

    for line in lines:
        if "31-" not in line:
            continue

        match = None
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                break
        if not match:
            continue

        company = match.group(1).strip()
        try:
            promoter = float(match.group(2))
            public = float(match.group(3))
            emp_raw = (match.group(4) or "").strip()
            employee = float(emp_raw) if emp_raw else 0.0
        except Exception:
            continue

        as_on = match.group(5)
        total = round(promoter + public + employee, 2)
        norm = normalize_company_name(company)

        matched_ticker = None
        if norm in company_to_ticker:
            matched_ticker = company_to_ticker[norm]
        else:
            for known_name, ticker in company_to_ticker.items():
                if norm == known_name or norm in known_name or known_name in norm:
                    matched_ticker = ticker
                    break

        rows.append(
            {
                "Company": company,
                "Ticker": matched_ticker,
                "PromoterPct_NSE": promoter,
                "PublicPct_NSE": public,
                "EmployeeTrustPct_NSE": employee,
                "OwnershipTotalPct": total,
                "OwnershipDataValid": abs(total - 100.0) <= 5.0,
                "ShareholdingAsOnDate": as_on,
                "ShareholdingSource": "NSE CSV",
            }
        )

    return pd.DataFrame(rows)


def apply_manual_overrides(lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    updated = dict(lookup)
    for ticker, data in MANUAL_SHAREHOLDING_OVERRIDES.items():
        if ticker not in updated:
            total = round(
                float(data["PromoterPct_NSE"]) + float(data["PublicPct_NSE"]) + float(data.get("EmployeeTrustPct_NSE", 0.0)),
                2,
            )
            updated[ticker] = {
                "PromoterPct_NSE": float(data["PromoterPct_NSE"]),
                "PublicPct_NSE": float(data["PublicPct_NSE"]),
                "EmployeeTrustPct_NSE": float(data.get("EmployeeTrustPct_NSE", 0.0)),
                "OwnershipTotalPct": total,
                "OwnershipDataValid": abs(total - 100.0) <= 5.0,
                "ShareholdingAsOnDate": data.get("ShareholdingAsOnDate"),
                "HasShareholdingData": True,
                "ShareholdingSource": data.get("Source", "Manual override"),
            }
    return updated


def build_shareholding_lookup(uploaded_file) -> Tuple[Dict[str, Dict[str, Any]], pd.DataFrame]:
    if uploaded_file is None:
        return apply_manual_overrides({}), pd.DataFrame()

    raw_bytes = uploaded_file.getvalue()
    text = raw_bytes.decode("utf-8", errors="ignore")
    parsed_df = parse_shareholding_text(text)

    lookup: Dict[str, Dict[str, Any]] = {}
    if not parsed_df.empty:
        matched_df = parsed_df[parsed_df["Ticker"].notna()].copy()
        for _, row in matched_df.iterrows():
            ticker = str(row["Ticker"]).upper().strip()
            lookup[ticker] = {
                "PromoterPct_NSE": float(row["PromoterPct_NSE"]),
                "PublicPct_NSE": float(row["PublicPct_NSE"]),
                "EmployeeTrustPct_NSE": float(row["EmployeeTrustPct_NSE"]),
                "OwnershipTotalPct": float(row["OwnershipTotalPct"]),
                "OwnershipDataValid": bool(row["OwnershipDataValid"]),
                "ShareholdingAsOnDate": row["ShareholdingAsOnDate"],
                "HasShareholdingData": True,
                "ShareholdingSource": row.get("ShareholdingSource", "NSE CSV"),
            }

    lookup = apply_manual_overrides(lookup)
    return lookup, parsed_df


def compute_screen_verdict(
    l1_val, l2_prof, l3_cf, l4_share, l5_forensic,
    l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing,
) -> str:
    missing_flags = [l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing]
    pass_flags = [l1_val, l2_prof, l3_cf, l4_share, l5_forensic]
    testable_count = sum(1 for m in missing_flags if not m)
    if testable_count < 3:
        return VERDICT_FAIL_NODATA
    if any((not passed) and (not missing) for passed, missing in zip(pass_flags, missing_flags)):
        return VERDICT_FAIL_GENUINE
    if any(missing_flags):
        return VERDICT_PASS_DATAGAP
    return VERDICT_PASS


def evaluate_stock(ticker: str) -> Dict[str, Any]:
    base_ticker = ticker.replace(".NS", "").upper().strip()
    try:
        info = yf.Ticker(ticker).info
        fund_row = fundamentals_lookup.get(base_ticker)
        sh_data = shareholding_lookup.get(base_ticker)

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
        mcap_raw = safe(info, "marketCap") or 0
        price = safe(info, "regularMarketPrice") or safe(info, "currentPrice")
        sector = safe(info, "sector", "N/A")
        ebit = safe(info, "ebit")
        ta = safe(info, "totalAssets")
        current_liab = safe(info, "totalCurrentLiabilities")

        mcap_cr = (mcap_raw / 1e7) if mcap_raw else None

        roce = None
        if ebit and ta and current_liab is not None:
            capital_employed = ta - current_liab
            if capital_employed and capital_employed > 0:
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
        if de is not None:
            try:
                de_ratio = float(de)
                if de_ratio > 10:
                    de_ratio = None
            except Exception:
                de_ratio = None

        if fund_row is not None:
            override_map = {
                "ROE_Latest": "roe",
                "ROCE_Latest": "roce",
                "OPM_Latest": "opm",
                "Revenue_CAGR_AllYears": "revg",
                "PAT_CAGR_AllYears": "earng",
            }
            for col, target in override_map.items():
                if col in fund_row.index:
                    v = parse_percent_or_float(fund_row[col])
                    if v is not None:
                        if target == "roe":
                            roe = v
                        elif target == "roce":
                            roce = v
                        elif target == "opm":
                            opm = v
                        elif target == "revg":
                            revg = v
                        elif target == "earng":
                            earng = v

        promoter_pct_nse = None
        public_pct_nse = None
        emp_trust_pct_nse = None
        ownership_total_pct = None
        ownership_data_valid = False
        has_shareholding_data = False
        sh_as_on_date = None
        shareholding_status = "Not available"
        shareholding_source = None

        if sh_data is not None:
            promoter_pct_nse = sh_data.get("PromoterPct_NSE")
            public_pct_nse = sh_data.get("PublicPct_NSE")
            emp_trust_pct_nse = sh_data.get("EmployeeTrustPct_NSE")
            ownership_total_pct = sh_data.get("OwnershipTotalPct")
            ownership_data_valid = sh_data.get("OwnershipDataValid", False)
            has_shareholding_data = sh_data.get("HasShareholdingData", False)
            sh_as_on_date = sh_data.get("ShareholdingAsOnDate")
            shareholding_source = sh_data.get("ShareholdingSource", "NSE CSV")
            shareholding_status = shareholding_source

        if promoter_pct_nse is not None:
            l4_share = ownership_data_valid and (promoter_pct_nse / 100.0) >= CONFIG["promoter_min"]
            l4_data_missing = False
        else:
            l4_share = insider is not None and insider >= CONFIG["insider_min"]
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

        quality_raw = approx_quality_score(info)
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
            l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing,
        )

        weighted_score = 0
        weighted_score += 5 if pe is not None and pe < 20 else 0
        weighted_score += 5 if peg is not None and peg < 1 else 0
        weighted_score += 5 if ev_ebitda is not None and ev_ebitda < 12 else 0
        weighted_score += 3 if pb is not None and pb < 3 else 0
        weighted_score += 2 if mcap_cr is not None and 200 <= mcap_cr <= 5000 else 0
        weighted_score += 8 if roce is not None and roce > 0.20 else 0
        weighted_score += 6 if roe is not None and roe > 0.18 else 0
        weighted_score += 4 if roa is not None and roa > 0.10 else 0
        weighted_score += 4 if opm is not None and opm > 0.15 else 0
        weighted_score += 4 if revg is not None and revg > 0.15 else 0
        weighted_score += 4 if earng is not None and earng > 0.20 else 0
        weighted_score += 8 if ocf_pat is not None and ocf_pat > 0.8 else 0
        weighted_score += 6 if fcf_yield is not None and fcf_yield > 0.03 else 0
        weighted_score += 6 if de_ratio is not None and de_ratio < 0.5 else 0
        weighted_score += 5 if l4_share else 0
        weighted_score += min(round(10 * quality_raw / 7), 10)

        ownership_anomaly = None
        if promoter_pct_nse is not None and ownership_data_valid:
            if promoter_pct_nse < 25:
                ownership_anomaly = f"Low promoter holding: {promoter_pct_nse:.2f}%"
            elif public_pct_nse is not None and public_pct_nse > 70:
                ownership_anomaly = f"High public float: {public_pct_nse:.2f}%"

        return {
            "Ticker": base_ticker,
            "Sector": sector,
            "ScreenVerdict": verdict,
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
            "WeightedScore": weighted_score,
            "Pass": final_pass,
            "HasFundamentals": fund_row is not None,
            "HasShareholdingData": has_shareholding_data,
            "Error": None,
        }
    except Exception as exc:
        return {
            "Ticker": base_ticker,
            "Sector": None,
            "ScreenVerdict": VERDICT_FAIL_NODATA,
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
            "Error": str(exc),
        }


st.sidebar.header("Controls")
min_score = st.sidebar.slider("Minimum conviction score", 0, 5, 4)
only_pass = st.sidebar.checkbox("Show only final pass names", value=True)
show_datagap = st.sidebar.checkbox("Also show PASS (Data gaps present)", value=True)
max_stocks = st.sidebar.number_input("Max stocks to screen", min_value=10, max_value=500, value=50, step=10)
pause_between_calls = st.sidebar.slider("Pause between API calls (seconds)", min_value=0.0, max_value=1.0, value=0.2, step=0.1)
uploaded_nse_file = st.sidebar.file_uploader("Upload NSE EOD CSV", type=["csv"])
uploaded_sh_file = st.sidebar.file_uploader("Upload NSE shareholding pattern CSV", type=["csv"])

with st.expander("Implementation note", expanded=False):
    st.markdown(
        """
- This version prioritizes robustness over elegance.
- NSE shareholding CSV is parsed first.
- If a known ticker is still missed, a tiny explicit manual override fills the gap.
- Stocks with missing data are separated from genuine fails in `ScreenVerdict`.
        """
    )

fundamentals_df = load_csv_if_exists("fundamentals_master.csv")
stock_master_df = load_csv_if_exists("stock_master.csv")
rebuild_fundamentals_lookup(fundamentals_df)
shareholding_lookup, parsed_shareholding_df = build_shareholding_lookup(uploaded_sh_file)

st.subheader("Data previews")
with st.expander("Shareholding parse preview", expanded=False):
    if parsed_shareholding_df.empty:
        st.info("No shareholding rows parsed yet. Upload the NSE shareholding file.")
    else:
        st.dataframe(parsed_shareholding_df.head(20), use_container_width=True)
        matched = parsed_shareholding_df[parsed_shareholding_df["Ticker"].notna()]
        if not matched.empty:
            st.write("Matched rows")
            st.dataframe(
                matched[["Ticker", "Company", "PromoterPct_NSE", "PublicPct_NSE", "EmployeeTrustPct_NSE"]],
                use_container_width=True,
            )

    override_rows = []
    for k, v in MANUAL_SHAREHOLDING_OVERRIDES.items():
        override_rows.append({"Ticker": k, **v})
    st.write("Manual ownership overrides")
    st.dataframe(pd.DataFrame(override_rows), use_container_width=True)

nse_prices_df = pd.DataFrame()
equity_universe_df = pd.DataFrame()
if uploaded_nse_file is not None:
    try:
        nse_prices_df = pd.read_csv(uploaded_nse_file)
        equity_universe_df = build_nse_equity_universe(nse_prices_df)
    except Exception as exc:
        st.error(f"Could not read NSE price CSV: {exc}")

with st.expander("Master file preview", expanded=False):
    if not fundamentals_df.empty:
        st.write("fundamentals_master.csv")
        st.dataframe(fundamentals_df.head(10), use_container_width=True)
    if not stock_master_df.empty:
        st.write("stock_master.csv")
        st.dataframe(stock_master_df.head(10), use_container_width=True)
    if not equity_universe_df.empty:
        st.write("NSE turnover-ranked universe")
        st.dataframe(equity_universe_df.head(20), use_container_width=True)


if st.button("Run live screen"):
    if not equity_universe_df.empty:
        tickers_to_screen = [f"{t}.NS" for t in equity_universe_df.head(int(max_stocks))["Ticker"].tolist()]
        st.info(f"Using turnover-ranked NSE universe: {len(tickers_to_screen)} stocks.")
    else:
        tickers_to_screen = DEFAULT_UNIVERSE[:]
        st.warning("No valid NSE price CSV available. Falling back to default universe.")

    rows: List[Dict[str, Any]] = []
    progress = st.progress(0)
    status = st.empty()

    for idx, ticker in enumerate(tickers_to_screen, start=1):
        status.text(f"Screening {ticker} ({idx}/{len(tickers_to_screen)})")
        rows.append(evaluate_stock(ticker))
        progress.progress(idx / len(tickers_to_screen))
        time.sleep(pause_between_calls)

    status.text("Done.")
    df = pd.DataFrame(rows)

    if not df.empty and not stock_master_df.empty and "Ticker" in stock_master_df.columns:
        merge_cols = [c for c in ["Ticker", "Sector", "SubSector"] if c in stock_master_df.columns]
        df = df.merge(stock_master_df[merge_cols], on="Ticker", how="left", suffixes=("", "_stock"))
        if "Sector_stock" in df.columns:
            df["Sector"] = df["Sector_stock"].combine_first(df["Sector"])
            df.drop(columns=["Sector_stock"], inplace=True)
        if "SubSector" not in df.columns and "SubSector_stock" in df.columns:
            df.rename(columns={"SubSector_stock": "SubSector"}, inplace=True)

    if not df.empty and not fundamentals_df.empty and "Ticker" in fundamentals_df.columns:
        fcols = [
            "Ticker", "Latest_Year", "ROE_Latest", "ROCE_Latest", "OPM_Latest", "NPM_Latest",
            "Revenue_CAGR_AllYears", "PAT_CAGR_AllYears", "ROCE_5Y_Avg", "ROE_5Y_Avg", "OPM_5Y_Avg",
            "OneOff_ROCE_Flag", "Asset_Quality_Risk_Flag", "Reg_Risk_Flag", "Gov_Risk_Flag",
        ]
        fcols = [c for c in fcols if c in fundamentals_df.columns]
        df = df.merge(fundamentals_df[fcols], on="Ticker", how="left")

    if only_pass:
        allowed = [VERDICT_PASS, VERDICT_PASS_DATAGAP] if show_datagap else [VERDICT_PASS]
        df = df[df["ScreenVerdict"].isin(allowed)]
    if min_score > 0:
        df = df[df["Conviction"] >= min_score]

    verdict_order = {
        VERDICT_PASS: 0,
        VERDICT_PASS_DATAGAP: 1,
        VERDICT_FAIL_GENUINE: 2,
        VERDICT_FAIL_NODATA: 3,
    }
    if not df.empty:
        df["_sort"] = df["ScreenVerdict"].map(verdict_order).fillna(9)
        df = df.sort_values(["_sort", "WeightedScore", "Conviction"], ascending=[True, False, False]).drop(columns=["_sort"])

    st.subheader("Screen results")
    st.write(f"Rows shown: {len(df)}")
    if df.empty:
        st.warning("No stocks matched the current filters.")
    else:
        st.write("Columns in df:")
        st.write(list(df.columns))

        st.write("HINDZINC transposed row:")
        hind = df[df["Ticker"] == "HINDZINC"]
        if not hind.empty:
            st.dataframe(hind.T, use_container_width=True)
            
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download CSV",
            data=df.to_csv(index=False),
            file_name="100x_screener_results.csv",
            mime="text/csv",
        )
        if "OwnershipAnomaly" in df.columns:
            anomalies = df[df["OwnershipAnomaly"].notna()][["Ticker", "OwnershipAnomaly", "PromoterPct_NSE", "PublicPct_NSE"]]
            if not anomalies.empty:
                st.warning("Ownership anomalies")
                st.dataframe(anomalies, use_container_width=True)
else:
    st.info("Click 'Run live screen' to start.")
