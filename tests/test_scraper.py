import json
from pathlib import Path

import pytest

import db
import filters
from scraper.parse import ParseError, parse_advertisements, parse_bid_items, project_links
from scraper.run import run_scrape

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def ads():
    return load("advertisements_page.json")


@pytest.fixture
def details():
    return load("details_08-1N8804.json")


# --- parsing ------------------------------------------------------------------

def test_parses_all_advertised_projects(ads):
    projects, meta = parse_advertisements(ads)
    assert len(projects) == 52 == meta["count"]
    assert meta["max_count"] == 100


def test_project_fields(ads):
    p = {x["contract"]: x for x in parse_advertisements(ads)[0]}["07-375404"]
    assert p["district"] == "07"
    assert p["description"] == "Replace drainage systems."
    assert p["county_route_pm"] == "LA-405-13.6/29.5"
    assert p["county"] == "LA"
    assert p["advertised"] == "2026-10-19"
    assert p["bid_open"] == "2026-12-09"
    assert p["estimate"] == 10_300_000
    assert p["working_days"] == 570
    assert p["licenses"] == "C-42, C-13"
    assert p["mandatory_prebid"] is False


def test_flags_read_by_label_not_position(ads):
    by = {x["contract"]: x for x in parse_advertisements(ads)[0]}
    assert by["08-1L1704"]["mandatory_prebid"] is True
    assert by["06-1A8104"]["postponed"] is True
    assert by["06-1A8104"]["mandatory_prebid"] is False
    assert sum(p["mandatory_prebid"] for p in by.values()) == 8


def test_missing_widget_is_a_clear_error():
    with pytest.raises(ParseError):
        parse_advertisements({"result": {"containers": []}})


def test_links_built_from_contract():
    links = project_links("08-1N8804")
    assert links["bid_items"].endswith("id=cc_advertisement_details&ad_id=08-1N8804&focus=bidItems")


def test_parses_bid_items(details):
    items = parse_bid_items(details)
    assert len(items) == 73
    assert items[69] == {"line": 70, "code": "870200", "description": "LIGHTING SYSTEM",
                         "unit": "LS", "quantity": "1"}


# --- electrical filter --------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Hot Mix Asphalt, imported borrow and signal and lighting system.", ["signal", "lighting"]),
    ("Mod Sig & Ltg, Pvmt repair, SW, curb ramp", ["sig", "ltg"]),
    ("Replace bridge, widen Rdwy, railroad, drainage, Elect and landscape.", ["elect"]),
    ("Modify existing drainage, replace lighting systems, and install TMS.", ["lighting", "tms"]),
    ("Replace drainage systems.", []),
    ("Design signage upgrades", []),          # 'sig' must not match inside 'signage'
])
def test_keyword_hits(text, expected):
    assert filters.keyword_hits(text) == expected


def test_electrical_items_use_item_code_section(details):
    elec = filters.electrical_items(parse_bid_items(details))
    assert [i["code"] for i in elec] == ["870200", "870400", "870700"]
    # Portable changeable message sign is traffic control (section 12), not electrical.
    assert "128651" not in [i["code"] for i in elec]


# --- full scrape against fixtures --------------------------------------------

def widget_page(widget_id, data):
    return {"result": {"containers": [{"rows": [{"columns": [{"widgets": [
        {"widget": {"id": widget_id, "data": data}}]}]}]}]}}


class FakeFetcher:
    def __init__(self, ads, details, upcoming=None, summaries=None, pdfs=None):
        self.ads, self.details, self.detail_calls = ads, details, []
        self._upcoming = upcoming or widget_page("cc_advertisements", {"list": []})
        self._summaries = summaries or widget_page("cc_bid_summary_result", {"projects": {}})
        self.pdfs, self.attachment_calls = pdfs or {}, []

    def advertisements(self):
        return self.ads

    def upcoming(self):
        return self._upcoming

    def project_details(self, contract):
        self.detail_calls.append(contract)
        return self.details

    def bid_summaries(self):
        return self._summaries

    def attachment(self, sys_id):
        self.attachment_calls.append(sys_id)
        if sys_id not in self.pdfs:
            raise ValueError("expected a PDF, got text/html")
        return self.pdfs[sys_id]


def test_scrape_stores_projects_and_checks_bid_items_once(ads, details, tmp_path):
    path = tmp_path / "t.db"
    f = FakeFetcher(ads, details)
    first = run_scrape("test", fetcher=f, db_path=path)
    assert first["projects_found"] == 52 and first["new_projects"] == 52
    assert len(f.detail_calls) == len(set(f.detail_calls)) == 52

    second = run_scrape("test", fetcher=FakeFetcher(ads, details), db_path=path)
    assert second["new_projects"] == 0
    assert second["bid_items_fetched"] == 0   # nothing re-fetched without a new addendum

    with db.connect(path) as conn:
        p = {x["contract"]: x for x in db.list_projects(conn)}["08-1N8804"]
        assert p["keyword_hits"] == ["signal", "lighting"]
        assert len(p["electrical_items"]) == 3
        assert db.last_run(conn)["error"] is None


def test_status_is_kept_across_scrapes(ads, details, tmp_path):
    path = tmp_path / "t.db"
    run_scrape("test", fetcher=FakeFetcher(ads, details), db_path=path)
    with db.connect(path) as conn:
        assert db.set_status(conn, "07-367604", "reviewing")
    run_scrape("test", fetcher=FakeFetcher(ads, details), db_path=path)
    with db.connect(path) as conn:
        p = {x["contract"]: x for x in db.list_projects(conn)}["07-367604"]
        assert p["status"] == "reviewing"


def test_empty_list_does_not_wipe_data(ads, details, tmp_path):
    path = tmp_path / "t.db"
    run_scrape("test", fetcher=FakeFetcher(ads, details), db_path=path)
    empty = json.loads(json.dumps(ads))
    w = empty["result"]["containers"][0]["rows"][0]["columns"][0]["widgets"][2]["widget"]["data"]
    w["list"] = []
    with pytest.raises(RuntimeError):
        run_scrape("test", fetcher=FakeFetcher(empty, details), db_path=path)
    with db.connect(path) as conn:
        assert len(db.list_projects(conn)) == 52
        assert "empty" in db.last_run(conn)["error"]


def test_routine_tms_item_alone_is_not_electrical_scope():
    routine = [{"code": "870009", "description": "MAINTAINING EXISTING TRAFFIC MANAGEMENT SYSTEM ELEMENTS"}]
    real = routine + [{"code": "872134", "description": "MODIFYING RAMP METERING SYSTEMS"}]
    assert not filters.has_electrical_scope([], routine)
    assert filters.has_electrical_scope([], real)
    assert filters.has_electrical_scope(["signal"], None)
    assert not filters.has_electrical_scope([], None)
