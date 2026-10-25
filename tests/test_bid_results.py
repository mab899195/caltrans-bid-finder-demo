import json
from pathlib import Path

import pytest

import db
from scraper.bid_summary import ScannedPdf, parse_summary_list, parse_summary_pdf
from scraper.run import run_scrape
from tests.test_scraper import FakeFetcher, widget_page

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def p08():
    return parse_summary_pdf((FIXTURES / "bid_summary_08-0P0404.pdf").read_bytes())


@pytest.fixture(scope="module")
def p07():
    return parse_summary_pdf((FIXTURES / "bid_summary_07-395804.pdf").read_bytes())


# --- Bid Summary PDF ----------------------------------------------------------

def test_header_fields(p08):
    assert p08["contract"] == "08-0P0404"
    assert p08["bid_open"] == "2026-10-13"
    assert p08["county_route_pm"] == "SBd-2, 18, 38, 330-Var"
    assert p08["description"] == "Radar speed feedback sign systems, and traffic stripe."
    assert p08["engineers_estimate"] == 2_081_539.00


def test_all_bidders_ranked_with_amounts(p08, p07):
    assert len(p08["bidders"]) == 9
    assert p08["bidders"][0] == {"rank": 1, "amount": 1_643_125.00, "name": "PAVE-TECH, INC."}
    assert p08["bidders"][2]["name"] == "KANAAN CONSTRUCTION, INC."
    assert [b["rank"] for b in p08["bidders"]] == list(range(1, 10))
    # Bidders continue onto page 2; the low bidder's full name comes from its proposal page.
    assert len(p07["bidders"]) == 6
    assert p07["bidders"][0]["name"] == "PETERSON-CHASE GENERAL ENGINEERING CONSTRUCTION, INC."


def test_low_bidder_items_and_electrical_total(p08, p07):
    assert p08["item_count"] == 34
    assert p08["electrical_items"] == [{"no": 33, "code": "871400",
                                        "description": "RADAR SPEED FEEDBACK SIGN SYSTEMS",
                                        "unit": "LS", "quantity": "1", "amount": 260_510.00}]
    assert p08["electrical_total"] == 260_510.00
    assert p07["item_count"] == 49 and p07["electrical_items"] == []


def test_electrical_subcontractors_per_bidder(p08, p07):
    assert p08["electrical_subs"] == [
        {"bidder_rank": 1, "bidder": "PAVE-TECH, INC.", "sub": "DB ELECTRIC, INC.", "items": ["871400"]},
        {"bidder_rank": 2, "bidder": "GBA ENGINEERING, INC.", "sub": "DB ELECTRIC INC.", "items": ["871400"]},
        {"bidder_rank": 8, "bidder": "YAKAR", "sub": "SSD CONTRACTORS INC", "items": ["871400"]},
    ]
    assert p07["electrical_subs"] == []


def test_summary_list():
    entries = parse_summary_list(load("bid_summary_page.json"))
    assert len(entries) == 21
    e = next(x for x in entries if x["contract"] == "08-0P0404")
    assert e["bid_open"] == "2026-10-13" and len(e["sys_id"]) == 32


def test_a_plus_b_contract():
    r = parse_summary_pdf((FIXTURES / "bid_summary_03-0N4504_aplusb.pdf").read_bytes())
    assert len(r["bidders"]) == 9
    assert r["bidders"][0] == {"rank": 1, "amount": 3_845_903.02, "a_plus_b_total": 4_180_903.02,
                               "name": "BAY CITIES PAVING & GRADING, INC."}
    assert r["item_count"] == 88                      # last item before the A+B totals
    assert [i["code"] for i in r["electrical_items"]] == ["870009", "870300", "872131", "872142"]
    assert {"bidder_rank": 7, "bidder": "GHILOTTI BROS., INC.", "sub": "M & M ELECTRIC",
            "items": ["870009", "870300", "872131", "872142"]} in r["electrical_subs"]
    assert not any(x["sub"].endswith(" BID") for x in r["electrical_subs"])


def test_scanned_summary_is_detected():
    with pytest.raises(ScannedPdf):
        parse_summary_pdf((FIXTURES / "bid_summary_scanned_page1.pdf").read_bytes())


# --- scrape integration ------------------------------------------------------

def test_scrape_stores_upcoming_and_bid_results(tmp_path):
    page = load("bid_summary_page.json")
    entries = parse_summary_list(page)
    sid = next(x["sys_id"] for x in entries if x["contract"] == "08-0P0404")
    pdfs = {sid: (FIXTURES / "bid_summary_08-0P0404.pdf").read_bytes()}
    path = tmp_path / "t.db"

    f = FakeFetcher(load("advertisements_page.json"), load("details_08-1N8804.json"),
                    upcoming=load("upcoming_page.json"), summaries=page, pdfs=pdfs)
    r = run_scrape("test", fetcher=f, db_path=path)
    assert r["projects_found"] == 72                   # 52 advertised + 20 upcoming
    assert r["bid_results_fetched"] == 1               # the other 20 PDFs "fail" in the fake
    assert len(f.attachment_calls) == 21

    with db.connect(path) as conn:
        stages = {p["contract"]: p["stage"] for p in db.list_projects(conn)}
        assert stages["04-2J88U4"] == "upcoming" and stages["07-375404"] == "advertised"
        results, unreadable = db.list_bid_results(conn)
        assert [x["contract"] for x in results] == ["08-0P0404"]
        assert len(unreadable) == 20
        assert results[0]["bidders"][2]["name"] == "KANAAN CONSTRUCTION, INC."

    # Next run: stored and failed PDFs are not downloaded again.
    f2 = FakeFetcher(load("advertisements_page.json"), load("details_08-1N8804.json"),
                     upcoming=load("upcoming_page.json"), summaries=page, pdfs=pdfs)
    run_scrape("test", fetcher=f2, db_path=path)
    assert f2.attachment_calls == []


def test_upcoming_project_becomes_advertised(tmp_path):
    ads, up = load("advertisements_page.json"), load("upcoming_page.json")
    path = tmp_path / "t.db"
    run_scrape("test", fetcher=FakeFetcher(ads, load("details_08-1N8804.json"), upcoming=up), db_path=path)
    # Next week the upcoming project shows up on the advertised list instead.
    moved = json.loads(json.dumps(ads))
    lst = moved["result"]["containers"][0]["rows"][0]["columns"][0]["widgets"][2]["widget"]["data"]["list"]
    up_list = up["result"]["containers"][0]["rows"][0]["columns"][0]["widgets"][2]["widget"]["data"]["list"]
    lst.append(next(x for x in up_list if x["display_field"]["value"] == "04-2J88U4"))
    run_scrape("test", fetcher=FakeFetcher(moved, load("details_08-1N8804.json"),
                                           upcoming=widget_page("cc_advertisements", {"list": []})), db_path=path)
    with db.connect(path) as conn:
        p = {x["contract"]: x for x in db.list_projects(conn, include_inactive=True)}
        assert p["04-2J88U4"]["stage"] == "advertised" and p["04-2J88U4"]["active"]
        assert not p["01-0C72U4"]["active"]           # dropped off the upcoming list
