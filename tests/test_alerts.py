import json
from datetime import date
from pathlib import Path

import pytest

import alerts
import db
from scraper.run import run_scrape
from tests.test_scraper import FakeFetcher

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 24)  # the day the fixtures were captured


@pytest.fixture
def conn(tmp_path):
    ads = json.loads((FIXTURES / "advertisements_page.json").read_text())
    details = json.loads((FIXTURES / "details_08-1N8804.json").read_text())
    path = tmp_path / "t.db"
    run_scrape("test", fetcher=FakeFetcher(ads, details), db_path=path)
    with db.connect(path) as c:
        c.execute("DELETE FROM alerts")  # start clean; the scrape queued against the real date
        yield c


def project(conn, contract):
    return {p["contract"]: p for p in db.list_projects(conn)}[contract]


def test_default_filter_matches_district_estimate_and_electrical(conn):
    s = alerts.default_settings()
    # Every fixture project gets the same bid items (signal and lighting).
    assert alerts.matches(project(conn, "07-367604"), s, "2026-10-24")      # D07, $2.7M
    assert not alerts.matches(project(conn, "08-1N8804"), s, "2026-10-24")  # $3.53M, over max
    assert not alerts.matches(project(conn, "05-1R5704"), s, "2026-10-24")  # D05
    assert not alerts.matches(project(conn, "07-375204"), s, "2026-11-04")  # bid opened 11-03


def test_unchecked_bid_items_wait_for_next_run(conn):
    p = {**project(conn, "07-367604"), "electrical_items": None}
    assert not alerts.matches(p, alerts.default_settings(), "2026-10-24")


def test_one_email_per_project_and_never_twice(conn):
    first = alerts.queue_new_alerts(conn, TODAY)
    assert first == len(db.list_alerts(conn)) > 0
    assert len({a["contract"] for a in db.list_alerts(conn)}) == first
    assert alerts.queue_new_alerts(conn, TODAY) == 0


def test_widening_the_filter_only_adds_newly_matching_projects(conn):
    before = alerts.queue_new_alerts(conn, TODAY)
    alerts.save_settings(conn, {**alerts.default_settings(), "estimate_max": 4_000_000})
    added = alerts.queue_new_alerts(conn, TODAY)
    assert added > 0
    contracts = [a["contract"] for a in db.list_alerts(conn)]
    assert "08-1N8804" in contracts and len(contracts) == before + added


def test_settings_are_validated(conn):
    s = alerts.save_settings(conn, {"districts": ["08", "99", "07"], "estimate_min": "500000",
                                    "estimate_max": "", "electrical_only": 0})
    assert s == {"districts": ["07", "08"], "estimate_min": 500_000, "estimate_max": None,
                 "electrical_only": False}
    assert alerts.get_settings(conn) == s


def test_email_content_follows_prd(conn):
    subject, text, html = alerts.build_email(project(conn, "08-1L1704"), TODAY)
    assert subject == ("New Caltrans bid – 08-1L1704 – RHMA, cold plane AC pavement, "
                       "vegetation control (minor Conc) and MGS – $52,000,000")
    assert "Mandatory pre-bid: YES" in text
    assert "2026-12-01 (38 days left)" in text
    assert "870400  SIGNAL AND LIGHTING SYSTEM" in text
    for focus in ("bidItems", "planHolders", "subOptIns"):
        assert f"focus={focus}" in html and f"focus={focus}" in text
    assert "08 San Bernardino / Riverside" in html
