from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
RUNS_DIR = DATA_DIR / "runs"
MANUAL_COMPANY_DIR = DATA_DIR / "manual_uploads" / "company_reports"
MANUAL_SECTOR_DIR = DATA_DIR / "manual_uploads" / "sector_reports"
DOCS_DIR = BASE_DIR / "docs"

SCHEMA_VERSION = "phase2_v1"
CACHE_MAX_AGE_DAYS = 30
SCHEDULE_LABEL = "weekly"

CONFIG = {
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
