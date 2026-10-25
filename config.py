"""Settings for the Caltrans Bid Finder. Edit here to tune filters."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ON_VERCEL = bool(os.environ.get("VERCEL"))
# Local SQLite file. On Vercel set DATABASE_URL (Postgres); without it the app
# falls back to /tmp, which is wiped whenever the function restarts.
DB_PATH = Path(os.environ.get("BID_FINDER_DB") or
               ("/tmp/bids.db" if ON_VERCEL else BASE_DIR / "data" / "bids.db"))

# Minimum minutes between scrapes started from the "Refresh now" button.
REFRESH_COOLDOWN_MINUTES = int(os.environ.get("BID_FINDER_REFRESH_COOLDOWN", 15 if ON_VERCEL else 0))

# --- Source -----------------------------------------------------------------
SITE = "https://ppmoe.dot.ca.gov"
PAGE_API = f"{SITE}/api/now/sp/page"
LIST_PAGE_ID = "cc_advertisement"
DETAIL_PAGE_ID = "cc_advertisement_details"
BID_SUMMARY_PAGE_ID = "cc_bid_summary"
ATTACHMENT_API = f"{SITE}/api/x_cado2_contractor/public_attachment_downloader_api"

# Identify ourselves honestly. Set BID_FINDER_COMPANY to the contractor running
# this, and BID_FINDER_CONTACT to an email or phone so Caltrans can reach them.
COMPANY = os.environ.get("BID_FINDER_COMPANY", "")
CONTACT = os.environ.get("BID_FINDER_CONTACT", "")
USER_AGENT = "CaltransBidFinder/1.0 (daily read of advertised projects" + "".join(
    f"; {label}: {value}" for label, value in (("operator", COMPANY), ("contact", CONTACT)) if value
) + ")"
REQUEST_DELAY_SECONDS = 3   # pause between requests
REQUEST_TIMEOUT_SECONDS = 30
LIST_MAX_COUNT = 100        # the site caps the list at this many projects
# Stop optional fetches (bid items, bid summaries) after this long; the next
# run picks up where this one stopped. Vercel functions are capped at 300 s.
SCRAPE_TIME_BUDGET_SECONDS = int(os.environ.get("BID_FINDER_TIME_BUDGET", 240))

# --- Districts ----------------------------------------------------------------
DISTRICTS = {
    "01": "Eureka (North Coast)",
    "02": "Redding",
    "03": "Marysville / Sacramento",
    "04": "Oakland (Bay Area)",
    "05": "San Luis Obispo (Central Coast)",
    "06": "Fresno / Bakersfield",
    "07": "Los Angeles / Ventura",
    "08": "San Bernardino / Riverside",
    "09": "Bishop (Inyo / Mono)",
    "10": "Stockton",
    "11": "San Diego / Imperial",
    "12": "Orange County",
}

# --- Alerts -------------------------------------------------------------------
# "demo": emails are built and saved to the in-app outbox, never sent.
ALERT_MODE = "demo"
ALERT_RECIPIENT = os.environ.get("BID_FINDER_ALERT_TO", "you@yourcompany.com")
APP_URL = os.environ.get("BID_FINDER_APP_URL", "http://127.0.0.1:5050")

# --- Default filters (all editable on the page) ------------------------------
TARGET_DISTRICTS = ["07", "08"]
ESTIMATE_MIN = 1_000_000
ESTIMATE_MAX = 3_000_000

# Bid items are fetched for projects in these districts: once per project,
# and again only when its addenda count changes.
BID_ITEM_DISTRICTS = list(DISTRICTS)

# Matched case-insensitively as whole words/phrases in the short description.
# Includes the abbreviations Caltrans uses in descriptions (Sig, Ltg, Elect).
# TODO: confirm this list with the field team (PRD open item).
ELECTRICAL_KEYWORDS = [
    "signal", "signals", "sig",
    "lighting", "ltg",
    "tms", "traffic management",
    "fiber optic", "communication system", "communication systems",
    "ramp meter", "ramp metering", "loop detector", "loop detectors",
    "cctv", "changeable message sign", "cms",
    "electrical", "elect", "elec",
    "conduit", "pull box", "service equipment",
    "flashing beacon", "rrfb", "rectangular rapid fb",
]

# Caltrans bid item codes are 6 digits; the first 2 are the Standard
# Specifications section. Sections 86 and 87 are Electrical Systems.
ELECTRICAL_ITEM_SECTIONS = ["86", "87"]

# Electrical items that appear on most jobs and aren't real electrical scope.
# They are still shown, but a project with only these isn't flagged electrical.
MINOR_ELECTRICAL_ITEMS = [
    "870009",  # Maintaining existing traffic management system elements during construction
]
