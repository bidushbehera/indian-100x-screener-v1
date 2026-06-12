import argparse
import json
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
RUNS_DIR = DATA_DIR / "runs"
MANUAL_COMPANY_DIR = DATA_DIR / "manual_uploads" / "company_reports"
MANUAL_SECTOR_DIR = DATA_DIR / "manual_uploads" / "sector_reports"

for p in [CACHE_DIR, RUNS_DIR, MANUAL_COMPANY_DIR, MANUAL_SECTOR_DIR]:
    p.mkdir(parents=True, exist_ok=True)

SCHEMA_VERSION = "phase2_v2"
CACHE_MAX_AGE_DAYS = 30

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
    "ocf_pat_min_guard": 0.50,
    "de_max_guard": 1.00,
    "promoter_min": 0.40,
    "insider_min": 0.40,
    "quality_min_guard": 4,
}

DEFAULT_UNIVERSE = [
    "LLOYDSME.NS", "POLYCAB.NS", "DEEPAKNTR.NS", "CGPOWER.NS", "TANLA.NS",
    "KPITTECH.NS", "CDSL.NS", "CAMS.NS", "IRCTC.NS", "OLECTRA.NS",
]

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
}

QUARTERLY_REPORT_SOURCES = [
    ("NSE Financial Results", "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"),
    ("NSE Annual Reports", "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"),
    ("SEBI Corporate Filings", "https://www.sebi.gov.in/curation/corporate_filings.html"),
    ("NSE Financials", "https://www.nseindia.com/static/investor-relations/financials"),
    ("Company Investor Relations", "Official company investor-relations page"),
]

SECTOR_REPORT_SOURCES = [
    ("NITI Aayog Reports", "https://niti.gov.in/publications/division-reports"),
    ("RBI Publications", "https://www.rbi.org.in/scripts/publications.aspx"),
    ("SEBI Reports", "https://www.sebi.gov.in/reports-and-statistics/reports.html"),
    ("Industry Associations", "Use the relevant free public association or regulator portal"),
    ("Ministry / Department Portals", "Use the relevant ministry/department publication page"),
]

SH_COL_COMPANY = "COMPANY"
SH_COL_PROMOTER = "PROMOTER & PROMOTER GROUP (A)"
SH_COL_PUBLIC = "PUBLIC (B)"
SH_COL_EMP_TRUST = "SHARES HELD BY EMPLOYEE TRUSTS (C2)"
SH_COL_AS_ON = "AS ON DATE"
SH_COL_REVISION = "REVISION DATE"
SH_COL_ACTION = "ACTION"

VERDICT_PASS = "PASS"
VERDICT_PASS_DATAGAP = "PASS (Data gaps present)"
VERDICT_FAIL_GENUINE = "FAIL (Genuine)"
VERDICT_FAIL_NODATA = "FAIL (Insufficient data)"


@dataclass
class FetchResult:
    path: Optional[Path]
    status: str
    message: str
    fetched_at: Optional[str] = None


def utcnow():
    return datetime.now(timezone.utc)


def iso_now():
    return utcnow().isoformat()


def safe(info: Dict[str, Any], key: str, default=None):
    if info is None:
        return default
    v = info.get(key, default)
    if v in (None, "N/A", "NaN"):
        return default
    return v


def parse_percent_or_float(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip().replace("%", "").replace(",", "")
        if not text:
            return None
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


def parse_float(value):
    if value is None or pd.isna(value):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def file_age_days(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    age = utcnow() - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return age.days


def is_cache_fresh(path: Path) -> bool:
    age = file_age_days(path)
    return age is not None and age <= CACHE_MAX_AGE_DAYS


def send_email(subject: str, html_body: str):
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    to_email = os.getenv("ALERT_EMAIL_TO")
    from_email = os.getenv("ALERT_EMAIL_FROM") or username

    if not all([host, port, username, password, to_email, from_email]):
        return False, "Email settings missing"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(host, int(port)) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, str(e)


def try_download(url: str, dest: Path, timeout: int = 30) -> FetchResult:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/zip,application/octet-stream,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return FetchResult(None, "failed", f"HTTP {r.status_code}")
        if "text/html" in r.headers.get("Content-Type", "") and "<html" in r.text[:500].lower():
            return FetchResult(None, "failed", "Received HTML page instead of data file")
        dest.write_bytes(r.content)
        return FetchResult(dest, "ok", "downloaded", iso_now())
    except Exception as e:
        return FetchResult(None, "failed", str(e))


def latest_cache_or_none(prefix: str) -> Optional[Path]:
    files = sorted(CACHE_DIR.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def fetch_bhavcopy() -> FetchResult:
    today = utcnow().strftime("cm%d%b%Ybhav.csv.zip").lower()
    dest = CACHE_DIR / today
    url = f"https://nsearchives.nseindia.com/content/cm/{today}"
    result = try_download(url, dest)
    if result.status == "ok":
        return result
    cache = latest_cache_or_none("cm")
    if cache and is_cache_fresh(cache):
        return FetchResult(cache, "cache", "Using cached bhavcopy within 30-day freshness window", iso_now())
    return FetchResult(None, "manual_required", "Bhavcopy auto-fetch failed and no fresh cache <= 30 days is available")


def fetch_shareholding() -> FetchResult:
    cache = latest_cache_or_none("shareholding")
    if cache and is_cache_fresh(cache):
        return FetchResult(cache, "cache", "Using latest cached shareholding within 30-day freshness window", iso_now())
    return FetchResult(None, "manual_required", "Shareholding auto-fetch not implemented reliably; upload latest source file")


def normalise_company_name(s: str) -> str:
    if s is None:
        return ""
    s = str(s).upper().strip()
    for token in ["LIMITED", "LTD", "LIMITED.", "LTD.", "INDIA", "(INDIA)", "INDIAN", "PRIVATE", "PVT", "PVT.", "&", ",", ".", "-", "/", "(", ")"]:
        s = s.replace(token, " ")
    return " ".join(s.split())


def build_nse_equity_universe(nse_df: pd.DataFrame) -> pd.DataFrame:
    if nse_df is None or nse_df.empty:
        return pd.DataFrame()

    required_cols = ["FinInstrmTp", "SctySrs", "TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]
    if any(c not in nse_df.columns for c in required_cols):
        return pd.DataFrame()

    df = nse_df.copy()
    df = df[(df["FinInstrmTp"] == "STK") & (df["SctySrs"] == "EQ")]
    if df.empty:
        return pd.DataFrame()

    df = df[["TckrSymb", "ClsPric", "TtlTradgVol", "TtlTrfVal"]].copy()
    df.columns = ["Ticker", "Close", "Volume", "Turnover"]
    df["Ticker"] = df["Ticker"].astype(str).str.upper()
    return df.sort_values("Turnover", ascending=False).reset_index(drop=True)


def load_csv_if_exists(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def load_fundamentals_master() -> pd.DataFrame:
    return load_csv_if_exists(BASE_DIR / "fundamentals_master.csv")


def load_stock_master() -> pd.DataFrame:
    return load_csv_if_exists(BASE_DIR / "stock_master.csv")


def build_fundamentals_lookup(fundamentals_master_df: pd.DataFrame) -> Dict[str, Any]:
    lookup = {}
    if fundamentals_master_df is None or fundamentals_master_df.empty or "Ticker" not in fundamentals_master_df.columns:
        return lookup
    tmp = fundamentals_master_df.copy()
    tmp["TickerKey"] = tmp["Ticker"].astype(str).str.upper()
    lookup = {row["TickerKey"]: row for _, row in tmp.iterrows()}
    return lookup


def build_shareholding_lookup(shareholding_df: pd.DataFrame, stock_master_df: Optional[pd.DataFrame] = None) -> Dict[str, Dict]:
    lookup = {}
    if shareholding_df is None or shareholding_df.empty:
        return lookup

    df = shareholding_df.copy()
    df.columns = [str(c).strip().lstrip("\ufeff").strip('"') for c in df.columns]

    if SH_COL_COMPANY not in df.columns:
        return lookup

    company_to_row = {}
    for _, row in df.iterrows():
        cname = str(row[SH_COL_COMPANY]).strip()
        key = normalise_company_name(cname)
        if key and key not in company_to_row:
            company_to_row[key] = row

    stock_name_to_ticker = {}
    if stock_master_df is not None and not stock_master_df.empty:
        temp = stock_master_df.copy()
        temp.columns = [str(c).strip() for c in temp.columns]
        possible_name_cols = ["Company", "CompanyName", "Company Name", "Name"]
        name_col = next((c for c in possible_name_cols if c in temp.columns), None)

        if name_col is not None and "Ticker" in temp.columns:
            for _, row in temp.iterrows():
                t = str(row["Ticker"]).strip().upper()
                cname = str(row[name_col]).strip()
                nkey = normalise_company_name(cname)
                if t and nkey and nkey not in stock_name_to_ticker:
                    stock_name_to_ticker[nkey] = t

    for ticker, company_name in TICKER_TO_COMPANY.items():
        nkey = normalise_company_name(company_name)
        if nkey and nkey not in stock_name_to_ticker:
            stock_name_to_ticker[nkey] = ticker

    for nkey, row in company_to_row.items():
        ticker = stock_name_to_ticker.get(nkey)

        if ticker is None:
            for stock_key, stock_ticker in stock_name_to_ticker.items():
                if len(nkey) >= 8 and (nkey in stock_key or stock_key in nkey):
                    ticker = stock_ticker
                    break

        if ticker is None:
            continue

        def pct_val(col_name: str) -> Optional[float]:
            if col_name not in row.index:
                return None
            v = row[col_name]
            try:
                return float(str(v).replace("%", "").replace(",", "").strip())
            except Exception:
                return None

        promoter_pct = pct_val(SH_COL_PROMOTER)
        public_pct = pct_val(SH_COL_PUBLIC)
        emp_pct = pct_val(SH_COL_EMP_TRUST)
        as_on_date = str(row[SH_COL_AS_ON]).strip() if SH_COL_AS_ON in row.index else None
        revision_date = str(row[SH_COL_REVISION]).strip() if SH_COL_REVISION in row.index else None
        action_link = str(row[SH_COL_ACTION]).strip() if SH_COL_ACTION in row.index else None

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
            "HasShareholdingData": True,
        }

    return lookup


def approx_quality_score_from_metrics(roe, roa, ocf_pat, de_ratio, opm, revg):
    score = 0
    if roe is not None and roe > 0.18:
        score += 1
    if roa is not None and roa > 0.05:
        score += 1
    if ocf_pat is not None and ocf_pat > 0:
        score += 1
    if ocf_pat is not None and ocf_pat >= 1.0:
        score += 1
    if de_ratio is not None and de_ratio <= 0.5:
        score += 1
    if opm is not None and opm > 0.15:
        score += 1
    if revg is not None and revg > 0:
        score += 1
    return score


def compute_screen_verdict(l1_val, l2_prof, l3_guard, l4_share, l5_guard, l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing):
    layers_missing = [l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing]
    layers_pass = [l1_val, l2_prof, l3_guard, l4_share, l5_guard]
    testable_count = sum(1 for m in layers_missing if not m)

    if testable_count < 3:
        return VERDICT_FAIL_NODATA

    genuine_failure = any((not p) and (not m) for p, m in zip(layers_pass, layers_missing))
    if genuine_failure:
        return VERDICT_FAIL_GENUINE

    if any(layers_missing):
        return VERDICT_PASS_DATAGAP

    return VERDICT_PASS


def get_price_from_history(ticker: str) -> Tuple[Optional[float], Optional[str]]:
    try:
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            close = hist["Close"].dropna()
            if not close.empty:
                return float(close.iloc[-1]), None
        return None, "No price history"
    except Exception as e:
        return None, str(e)


def get_yahoo_fastinfo(ticker: str) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        tk = yf.Ticker(ticker)
        fi = getattr(tk, "fast_info", None)
        if fi is None:
            return {}, "fast_info unavailable"
        out = {}
        for key in ["market_cap", "last_price", "previous_close"]:
            try:
                out[key] = fi.get(key)
            except Exception:
                try:
                    out[key] = getattr(fi, key)
                except Exception:
                    pass
        return out, None
    except Exception as e:
        return {}, str(e)


def evaluate_stock(ticker: str, fundamentals_lookup: Dict[str, Any], shareholding_lookup: Dict[str, Dict], stock_master_df: pd.DataFrame) -> Dict[str, Any]:
    base_ticker = ticker.replace(".NS", "").upper()
    fund_row = fundamentals_lookup.get(base_ticker)
    sh_data = shareholding_lookup.get(base_ticker)
    errors = []

    stock_row = None
    if stock_master_df is not None and not stock_master_df.empty and "Ticker" in stock_master_df.columns:
        tmp = stock_master_df.copy()
        tmp["Ticker"] = tmp["Ticker"].astype(str).str.upper()
        match = tmp[tmp["Ticker"] == base_ticker]
        if not match.empty:
            stock_row = match.iloc[0]

    sector = None
    sub_sector = None
    if stock_row is not None:
        sector = stock_row["Sector"] if "Sector" in stock_row.index else None
        sub_sector = stock_row["SubSector"] if "SubSector" in stock_row.index else None

    pe = pb = peg = ev_ebitda = None
    roe = roce = roa = opm = revg = earng = None
    ocf_pat = fcf_yield = de_ratio = None
    price = None
    mcap_cr = None

    if fund_row is not None:
        if "ROE_Latest" in fund_row.index:
            roe = parse_percent_or_float(fund_row["ROE_Latest"])
        if "ROCE_Latest" in fund_row.index:
            roce = parse_percent_or_float(fund_row["ROCE_Latest"])
        if "OPM_Latest" in fund_row.index:
            opm = parse_percent_or_float(fund_row["OPM_Latest"])
        if "Revenue_CAGR_AllYears" in fund_row.index:
            revg = parse_percent_or_float(fund_row["Revenue_CAGR_AllYears"])
        if "PAT_CAGR_AllYears" in fund_row.index:
            earng = parse_percent_or_float(fund_row["PAT_CAGR_AllYears"])

        for col, var_name in [
            ("PE", "pe"),
            ("PB", "pb"),
            ("PEG", "peg"),
            ("EV_EBITDA", "ev_ebitda"),
            ("ROA_Latest", "roa"),
            ("OCF_PAT", "ocf_pat"),
            ("FCF_Yield", "fcf_yield"),
            ("DebtToEquity", "de_ratio"),
            ("MCap_Cr", "mcap_cr"),
        ]:
            if col in fund_row.index:
                v = parse_percent_or_float(fund_row[col]) if var_name in ["roa", "fcf_yield"] else parse_float(fund_row[col])
                if v is not None:
                    if var_name == "pe":
                        pe = v
                    elif var_name == "pb":
                        pb = v
                    elif var_name == "peg":
                        peg = v
                    elif var_name == "ev_ebitda":
                        ev_ebitda = v
                    elif var_name == "roa":
                        roa = v
                    elif var_name == "ocf_pat":
                        ocf_pat = v
                    elif var_name == "fcf_yield":
                        fcf_yield = v
                    elif var_name == "de_ratio":
                        de_ratio = v
                    elif var_name == "mcap_cr":
                        mcap_cr = v

    fastinfo, fi_err = get_yahoo_fastinfo(ticker)
    if fi_err:
        errors.append(f"fast_info: {fi_err}")

    if price is None:
        price = parse_float(fastinfo.get("last_price")) or parse_float(fastinfo.get("previous_close"))

    if mcap_cr is None:
        mcap = parse_float(fastinfo.get("market_cap"))
        if mcap is not None:
            mcap_cr = mcap / 1e7

    if price is None:
        hist_price, hist_err = get_price_from_history(ticker)
        if hist_err:
            errors.append(f"history: {hist_err}")
        price = hist_price

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
        ownership_score = 1.0 if l4_share else 0.0
    else:
        l4_share = False
        l4_data_missing = True
        ownership_score = 0.0

    quality_raw = approx_quality_score_from_metrics(roe, roa, ocf_pat, de_ratio, opm, revg)

    l1_checks = [
        pe is not None and pe < CONFIG["pe_max"],
        peg is not None and peg < CONFIG["peg_max"],
        ev_ebitda is not None and ev_ebitda < CONFIG["ev_ebitda_max"],
        pb is not None and pb < CONFIG["pb_max"],
        mcap_cr is not None,
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

    l3_data_missing = sum([ocf_pat is not None, fcf_yield is not None, de_ratio is not None]) < 2
    l5_data_missing = sum([roe is not None, roa is not None, opm is not None, revg is not None]) < 3

    l3_guard = (
        (ocf_pat is not None and ocf_pat >= CONFIG["ocf_pat_min_guard"])
        and
        (de_ratio is None or de_ratio <= CONFIG["de_max_guard"])
    )
    l5_guard = quality_raw >= CONFIG["quality_min_guard"]

    verdict = compute_screen_verdict(
        l1_val, l2_prof, l3_guard, l4_share, l5_guard,
        l1_data_missing, l2_data_missing, l3_data_missing, l4_data_missing, l5_data_missing
    )

    l3_score = 0.0
    if ocf_pat is not None:
        if ocf_pat >= 1.2:
            l3_score += 1.0
        elif ocf_pat >= 0.8:
            l3_score += 0.8
        elif ocf_pat >= 0.5:
            l3_score += 0.5

    if fcf_yield is not None:
        if fcf_yield >= 0.05:
            l3_score += 1.0
        elif fcf_yield >= 0.03:
            l3_score += 0.7
        elif fcf_yield > 0:
            l3_score += 0.4

    if de_ratio is not None:
        if de_ratio <= 0.3:
            l3_score += 1.0
        elif de_ratio <= 0.5:
            l3_score += 0.8
        elif de_ratio <= 1.0:
            l3_score += 0.4

    l3_score = min(l3_score / 3.0, 1.0)
    l5_score = min(quality_raw / 7.0, 1.0) if quality_raw is not None else 0.0

    conviction = sum([l1_val, l2_prof, l3_guard, l4_share, l5_guard])
    hard_pass = bool(l2_prof and l3_guard and l5_guard and conviction >= 4)

    weighted_score = 0.0
    weighted_score += 8 if l1_val else 0
    weighted_score += 14 if l2_prof else 0
    weighted_score += 4 * (
        1.0 if ocf_pat is not None and ocf_pat >= 0.8 else
        0.5 if ocf_pat is not None and ocf_pat >= 0.5 else 0.0
    )
    weighted_score += 3 * (
        1.0 if fcf_yield is not None and fcf_yield >= 0.03 else
        0.5 if fcf_yield is not None and fcf_yield > 0 else 0.0
    )
    weighted_score += 4 * (
        1.0 if de_ratio is not None and de_ratio <= 0.5 else
        0.5 if de_ratio is not None and de_ratio <= 1.0 else
        0.0 if de_ratio is not None else 0.25
    )
    weighted_score += 4 * l5_score
    weighted_score += 3 * ownership_score

    fail_reasons = []
    if not l1_val and not l1_data_missing:
        fail_reasons.append("L1 Valuation")
    if not l2_prof and not l2_data_missing:
        fail_reasons.append("L2 Profitability")
    if not l3_guard and not l3_data_missing:
        fail_reasons.append("L3 Guardrail")
    if not l4_share and not l4_data_missing:
        fail_reasons.append("L4 Shareholding")
    if not l5_guard and not l5_data_missing:
        fail_reasons.append("L5 Guardrail")

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

    error_msg = "; ".join([e for e in errors if e]) if errors else None

    return {
        "Ticker": base_ticker,
        "Sector": sector,
        "SubSector": sub_sector,
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
        "DebtToEquity_raw": round(de_ratio, 2) if de_ratio is not None else None,
        "PromoterPct_NSE": promoter_pct_nse,
        "PublicPct_NSE": public_pct_nse,
        "EmployeeTrustPct_NSE": emp_trust_pct_nse,
        "OwnershipTotalPct": ownership_total_pct,
        "OwnershipDataValid": ownership_data_valid,
        "ShareholdingStatus": shareholding_status,
        "ShareholdingAsOnDate": sh_as_on_date,
        "ShareholdingRevisionDate": sh_revision_date,
        "QualityScore_raw": quality_raw,
        "L1_Val": l1_val,
        "L2_Prof": l2_prof,
        "L3_Guard": l3_guard,
        "L5_Guard": l5_guard,
        "L3_Score_0to1": round(l3_score, 3),
        "L5_Score_0to1": round(l5_score, 3),
        "L4_Share": l4_share,
        "L1_DataMissing": l1_data_missing,
        "L2_DataMissing": l2_data_missing,
        "L3_DataMissing": l3_data_missing,
        "L4_DataMissing": l4_data_missing,
        "L5_DataMissing": l5_data_missing,
        "Conviction": conviction,
        "WeightedScore": round(weighted_score, 2),
        "Pass": hard_pass,
        "HasFundamentals": fund_row is not None,
        "HasShareholdingData": has_shareholding_data,
        "ShareholdingActionLink": sh_action_link,
        "Error": error_msg,
    }


def generate_research_note(row: pd.Series, sector_context_status: str) -> str:
    caution = []
    incomplete = []

    if pd.isna(row.get("OCF_PAT")):
        incomplete.append("Operating cash flow conversion unavailable")
    if pd.isna(row.get("FCFYield_pct")):
        incomplete.append("FCF yield unavailable")
    if row.get("FCFYield_pct") is not None and not pd.isna(row.get("FCFYield_pct")) and row.get("FCFYield_pct") < 0:
        caution.append("Negative FCF yield; verify whether capex-led or structural")
    if row.get("DataGapReasons"):
        incomplete.append(f"Data gaps flagged in screen: {row.get('DataGapReasons')}")

    note = []
    note.append(f"# {row['Ticker']}")
    note.append(f"- Verdict: {row.get('ScreenVerdict')}")
    note.append(f"- Weighted score: {row.get('WeightedScore')}")
    note.append(f"- Conviction: {row.get('Conviction')}")
    note.append(f"- Sector context status: {sector_context_status}")
    note.append("## Strength")
    note.append(f"- ROE: {row.get('ROE_pct')}")
    note.append(f"- ROA: {row.get('ROA_pct')}")
    note.append(f"- OPM: {row.get('OPM_pct')}")
    note.append("## Caution")
    note.extend([f"- {x}" for x in (caution or ["No major quantitative caution from current screen beyond manual validation needs"])])
    note.append("## Analysis incomplete")
    note.extend([f"- {x}" for x in (incomplete or ["Need latest quarterly report and sector/industry context validation"])])
    return "\n".join(note) + "\n"


def latest_run_dir() -> Optional[Path]:
    runs = sorted([p for p in RUNS_DIR.iterdir() if p.is_dir()], reverse=True)
    return runs[0] if runs else None


def previous_successful_run(current: Path) -> Optional[Path]:
    runs = sorted([p for p in RUNS_DIR.iterdir() if p.is_dir() and p != current], reverse=True)
    return runs[0] if runs else None


def compare_runs(current_df: pd.DataFrame, prev_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if prev_df is None or prev_df.empty:
        out = current_df[["Ticker", "ScreenVerdict", "WeightedScore", "Conviction"]].copy()
        out["ChangeType"] = "NEW_BASELINE"
        return out

    cur = current_df[["Ticker", "ScreenVerdict", "WeightedScore", "Conviction"]].copy()
    prev = prev_df[["Ticker", "ScreenVerdict", "WeightedScore", "Conviction"]].copy()
    merged = cur.merge(prev, on="Ticker", how="outer", suffixes=("_cur", "_prev"), indicator=True)

    changes = []
    for _, r in merged.iterrows():
        if r["_merge"] == "left_only":
            c = "NEW_NAME"
        elif r["_merge"] == "right_only":
            c = "DROPPED_FROM_UNIVERSE"
        elif r.get("ScreenVerdict_cur") != r.get("ScreenVerdict_prev"):
            c = f"{r.get('ScreenVerdict_prev')} -> {r.get('ScreenVerdict_cur')}"
        elif pd.notna(r.get("WeightedScore_cur")) and pd.notna(r.get("WeightedScore_prev")) and r.get("WeightedScore_cur") - r.get("WeightedScore_prev") >= 3:
            c = "UPGRADED_SCORE"
        elif pd.notna(r.get("WeightedScore_cur")) and pd.notna(r.get("WeightedScore_prev")) and r.get("WeightedScore_prev") - r.get("WeightedScore_cur") >= 3:
            c = "DOWNGRADED_SCORE"
        else:
            c = "UNCHANGED"
        changes.append(c)

    merged["ChangeType"] = changes
    return merged


def manual_intervention_items(pass_df: pd.DataFrame, quarterly_available: Dict[str, bool], sector_available: Dict[str, bool], automation_flags: Dict[str, Any]) -> Dict[str, List[str]]:
    out = {}
    for _, row in pass_df.iterrows():
        t = row["Ticker"]
        bullets = []

        if not quarterly_available.get(t, False):
            bullets.append("Latest quarterly report not auto-retrieved reliably. Please upload latest official quarterly result / presentation / filing.")
        if not sector_available.get(t, False):
            bullets.append("Sector or industry report not auto-ingested for this stock's sector. Please upload latest quarterly sector/industry report.")
        if row.get("ShareholdingStatus") != "NSE CSV":
            bullets.append("Shareholding did not resolve to NSE CSV; validate promoter ownership manually.")
        if automation_flags.get("bhavcopy_status") == "manual_required":
            bullets.append("Bhavcopy auto-fetch failed and no fresh cache within 30 days is available. Upload latest bhavcopy.")
        if automation_flags.get("shareholding_status") == "manual_required":
            bullets.append("Shareholding auto-fetch unavailable or stale beyond 30 days. Upload latest shareholding source file.")

        out[t] = bullets
    return out


def write_manual_email(pass_df: pd.DataFrame, intervention_map: Dict[str, List[str]], run_dir: Path):
    items = []
    for ticker, bullets in intervention_map.items():
        if not bullets:
            continue
        items.append(f"<h3>{ticker}</h3><ul>" + "".join([f"<li>{b}</li>" for b in bullets]) + "</ul>")

    if not items:
        return False, "No intervention needed"

    html = "<h2>Manual input needed for PASS stocks</h2>" + "".join(items)
    ok, msg = send_email("100X Screener: manual intervention needed", html)
    (run_dir / "manual_intervention_email_status.json").write_text(json.dumps({"sent": ok, "message": msg}, indent=2))
    return ok, msg


def save_uploaded_files(files, folder: Path, prefix: str):
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        if f is None:
            continue
        safe_name = f"{prefix}__{Path(f.name).name}"
        target = folder / safe_name
        target.write_bytes(f.getbuffer())
        saved.append(target.name)
    return saved


def run_pipeline(manual_bhavcopy_file=None, manual_shareholding_file=None) -> Tuple[Path, pd.DataFrame, Dict[str, Any]]:
    run_id = utcnow().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    automation_flags = {}

    bh = fetch_bhavcopy()
    sh = fetch_shareholding()

    if manual_bhavcopy_file is not None:
        manual_path = CACHE_DIR / f"manual_bhavcopy_{Path(manual_bhavcopy_file.name).name}"
        manual_path.write_bytes(manual_bhavcopy_file.getbuffer())
        bh = FetchResult(manual_path, "manual_upload", "Using manually uploaded bhavcopy", iso_now())

    if manual_shareholding_file is not None:
        manual_path = CACHE_DIR / f"shareholding_{Path(manual_shareholding_file.name).name}"
        manual_path.write_bytes(manual_shareholding_file.getbuffer())
        sh = FetchResult(manual_path, "manual_upload", "Using manually uploaded shareholding file", iso_now())

    automation_flags["bhavcopy_status"] = bh.status
    automation_flags["shareholding_status"] = sh.status
    automation_flags["bhavcopy_message"] = bh.message
    automation_flags["shareholding_message"] = sh.message

    bhav_df = pd.DataFrame()
    if bh.path and bh.path.exists():
        try:
            if bh.path.suffix.lower() == ".csv":
                bhav_df = pd.read_csv(bh.path)
        except Exception:
            bhav_df = pd.DataFrame()

    universe_df = build_nse_equity_universe(bhav_df)
    if universe_df.empty:
        tickers = DEFAULT_UNIVERSE
        automation_flags["universe_mode"] = "default_universe"
    else:
        tickers = [f"{t}.NS" for t in universe_df.head(500)["Ticker"].tolist()]
        automation_flags["universe_mode"] = "bhavcopy"

    stock_master_df = load_stock_master()
    fundamentals_master_df = load_fundamentals_master()
    fundamentals_lookup = build_fundamentals_lookup(fundamentals_master_df)

    shareholding_lookup = {}
    if sh.path and sh.path.exists() and sh.path.suffix.lower() == ".csv":
        shareholding_lookup = build_shareholding_lookup(load_csv_if_exists(sh.path), stock_master_df)

    rows = [evaluate_stock(t, fundamentals_lookup, shareholding_lookup, stock_master_df) for t in tickers]
    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(["WeightedScore", "Conviction"], ascending=[False, False])

    df.to_csv(run_dir / "screen_results.csv", index=False)

    prev_dir = previous_successful_run(run_dir)
    prev_df = load_csv_if_exists(prev_dir / "screen_results.csv") if prev_dir else pd.DataFrame()
    delta_df = compare_runs(df, prev_df)
    delta_df.to_csv(run_dir / "delta_vs_previous.csv", index=False)

    pass_df = df[df["ScreenVerdict"] == VERDICT_PASS].copy() if not df.empty else pd.DataFrame()

    quarterly_available = {}
    sector_available = {}

    if not pass_df.empty:
        for t in pass_df["Ticker"].tolist():
            company_dir = MANUAL_COMPANY_DIR / t
            sector_dir = MANUAL_SECTOR_DIR / t
            company_dir.mkdir(parents=True, exist_ok=True)
            sector_dir.mkdir(parents=True, exist_ok=True)
            quarterly_available[t] = any(company_dir.glob("*"))
            sector_available[t] = any(sector_dir.glob("*"))

    intervention_map = manual_intervention_items(pass_df, quarterly_available, sector_available, automation_flags)
    (run_dir / "manual_intervention.json").write_text(json.dumps(intervention_map, indent=2))

    notes_dir = run_dir / "notes"
    notes_dir.mkdir(exist_ok=True)

    for _, row in pass_df.iterrows():
        ticker = row["Ticker"]
        sector_status = "available" if sector_available.get(ticker, False) else "missing_manual_upload"
        (notes_dir / f"{ticker}.md").write_text(generate_research_note(row, sector_status))

    write_manual_email(pass_df, intervention_map, run_dir)

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": iso_now(),
        "bhavcopy": bh.__dict__,
        "shareholding": sh.__dict__,
        "universe_mode": automation_flags["universe_mode"],
        "pass_count": int((df["ScreenVerdict"] == VERDICT_PASS).sum()) if not df.empty else 0,
        "datagap_count": int((df["ScreenVerdict"] == VERDICT_PASS_DATAGAP).sum()) if not df.empty else 0,
        "genuine_fail_count": int((df["ScreenVerdict"] == VERDICT_FAIL_GENUINE).sum()) if not df.empty else 0,
        "nodata_count": int((df["ScreenVerdict"] == VERDICT_FAIL_NODATA).sum()) if not df.empty else 0,
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    return run_dir, df, metadata


def render_app():
    st.set_page_config(page_title="100X Screener Phase 2", layout="wide")

    st.title("100X Screener Phase 2")
    st.caption("Automated screening, change tracking, research notes, and manual intervention workflow")

    st.subheader("Manual source upload for this run")
    uploaded_bhavcopy = st.file_uploader("Upload latest bhavcopy CSV if automatic fetch fails", type=["csv"], key="manual_bhavcopy")
    uploaded_shareholding = st.file_uploader("Upload latest shareholding CSV if automatic fetch fails", type=["csv"], key="manual_shareholding")

    if st.button("Run automation now"):
        with st.spinner("Running pipeline..."):
            run_dir, df, metadata = run_pipeline(
                manual_bhavcopy_file=uploaded_bhavcopy,
                manual_shareholding_file=uploaded_shareholding,
            )
        st.success(f"Run completed: {run_dir.name}")
        st.json(metadata)

    latest = latest_run_dir()
    if not latest:
        st.warning("No run found yet. Upload data if needed and click 'Run automation now'.")
        return

    st.subheader("Latest run")
    metadata_path = latest / "run_metadata.json"
    if metadata_path.exists():
        st.json(json.loads(metadata_path.read_text()))

    results = load_csv_if_exists(latest / "screen_results.csv")
    delta = load_csv_if_exists(latest / "delta_vs_previous.csv")
    manual_json = latest / "manual_intervention.json"
    interventions = json.loads(manual_json.read_text()) if manual_json.exists() else {}

    if not results.empty:
        st.subheader("Results")
        st.dataframe(results, use_container_width=True)

    if not delta.empty:
        st.subheader("Delta vs previous")
        st.dataframe(delta, use_container_width=True)

    st.subheader("Automation failures and manual intervention needed")
    pass_names = results[results["ScreenVerdict"] == VERDICT_PASS]["Ticker"].tolist() if not results.empty and "ScreenVerdict" in results.columns else []

    if not pass_names:
        st.info("No clean PASS names in latest run.")
    else:
        for ticker in pass_names:
            bullets = interventions.get(ticker, [])
            company_dir = MANUAL_COMPANY_DIR / ticker
            sector_dir = MANUAL_SECTOR_DIR / ticker
            company_dir.mkdir(parents=True, exist_ok=True)
            sector_dir.mkdir(parents=True, exist_ok=True)

            with st.expander(f"{ticker}", expanded=True):
                if bullets:
                    for b in bullets:
                        st.markdown(f"- {b}")
                else:
                    st.markdown("- No manual intervention currently required.")

                st.markdown("**Upload latest quarterly company files**")
                quarterly_files = st.file_uploader(
                    f"Upload quarterly files for {ticker}",
                    accept_multiple_files=True,
                    key=f"company_{ticker}"
                )
                if quarterly_files:
                    saved = save_uploaded_files(quarterly_files, company_dir, ticker)
                    st.success(f"Saved quarterly files: {', '.join(saved)}")

                st.markdown("**5 sources to obtain company quarterly reports**")
                for name, url in QUARTERLY_REPORT_SOURCES:
                    st.markdown(f"- [{name}]({url})" if url.startswith("http") else f"- {name}: {url}")

                st.markdown("**Upload latest sector / industry report**")
                sector_files = st.file_uploader(
                    f"Upload sector reports for {ticker}",
                    accept_multiple_files=True,
                    key=f"sector_{ticker}"
                )
                if sector_files:
                    saved = save_uploaded_files(sector_files, sector_dir, ticker)
                    st.success(f"Saved sector files: {', '.join(saved)}")

                st.markdown("**5 sources to obtain sector / industry reports**")
                for name, url in SECTOR_REPORT_SOURCES:
                    st.markdown(f"- [{name}]({url})" if url.startswith("http") else f"- {name}: {url}")

    notes_dir = latest / "notes"
    if notes_dir.exists():
        notes = sorted(notes_dir.glob("*.md"))
        st.subheader("Research notes")
        if not notes:
            st.info("No research notes generated because there are no clean PASS names in the latest run.")
        else:
            for note in notes:
                with st.expander(note.stem):
                    st.markdown(note.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduled", action="store_true")
    args, _ = parser.parse_known_args()

    if args.scheduled:
        run_pipeline()
    else:
        render_app()


if __name__ == "__main__":
    main()
