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
