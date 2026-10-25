"""Turn Contractors Corner page JSON into plain project / bid item dicts.

The site is a ServiceNow portal. The page API returns each widget's data;
records hold a ``display_field`` plus a list of ``secondary_fields``. A field
only carries a ``label`` when it has a value, so fields are looked up by label,
never by position.
"""
from urllib.parse import urlencode

import config


class ParseError(Exception):
    pass


def _widgets(page_json):
    result = page_json.get("result", page_json)
    for container in result.get("containers", []):
        for row in container.get("rows", []):
            for column in row.get("columns", []):
                for widget in column.get("widgets", []):
                    w = widget.get("widget") or {}
                    yield w.get("id"), (w.get("data") or {})


def widget_data(page_json, widget_id):
    for wid, data in _widgets(page_json):
        if wid == widget_id:
            return data
    raise ParseError(f"widget {widget_id!r} not found; the site layout may have changed")


def _fields(record):
    return {f["label"]: f for f in record.get("secondary_fields", []) if f.get("label")}


def _value(fields, label, default=None):
    # Some records keep their text only in display_value (value is None).
    f = fields.get(label) or {}
    for key in ("value", "display_value"):
        if f.get(key) not in (None, ""):
            return f[key]
    return default


def _flag(fields, label):
    return str(_value(fields, label, "0")).lower() in ("1", "true")


def _int(text):
    try:
        return int(float(str(text).replace(",", "")))
    except (TypeError, ValueError):
        return None


def detail_url(contract, focus=None):
    params = {"id": config.DETAIL_PAGE_ID, "ad_id": contract}
    if focus:
        params["focus"] = focus
    return f"{config.SITE}/cc?{urlencode(params)}"


def project_links(contract):
    return {
        "listing": detail_url(contract),
        "bid_items": detail_url(contract, "bidItems"),
        "planholders": detail_url(contract, "planHolders"),
        "bidder_inquiries": detail_url(contract, "bidInquiries"),
        "opt_in": detail_url(contract, "subOptIns"),
    }


def parse_advertisements(page_json):
    """Return (projects, meta) from the cc_advertisement page JSON."""
    data = widget_data(page_json, "cc_advertisements")
    records = data.get("list")
    if records is None:
        raise ParseError("advertisement widget has no 'list'")

    projects = []
    for rec in records:
        contract = (rec.get("display_field") or {}).get("value", "").strip()
        if not contract:
            continue
        f = _fields(rec)
        cr_pm = _value(f, "County-Route-PM", "")
        projects.append({
            "contract": contract,
            "district": contract[:2],
            "sys_id": rec.get("sys_id"),
            "description": _value(f, "Work Description", ""),
            "county_route_pm": cr_pm,
            "county": cr_pm.split("-")[0] if cr_pm else "",
            "location": _value(f, "Location Description", ""),
            "advertised": _value(f, "Advertisement Date"),
            "bid_open": _value(f, "Bid Open Date"),
            "estimate": _int(_value(f, "Call Out Number")),
            "working_days": _int(_value(f, "Total Working Days")),
            "goal": (f.get("Type of Goal") or {}).get("display_value", ""),
            "licenses": rec.get("licenses", ""),
            "mandatory_prebid": _flag(f, "Mandatory Pre-Bid Meeting"),
            "postponed": _flag(f, "Postponed"),
            "addenda_count": _int(rec.get("addendaCount")) or 0,
            "inquiry_count": _int(rec.get("inquiryCount")) or 0,
        })

    meta = {"count": _int(data.get("count")), "max_count": _int(data.get("maxCount"))}
    return projects, meta


def parse_bid_items(page_json):
    """Return the bid item list from a cc_advertisement_details page JSON."""
    data = widget_data(page_json, "cc_detail_bid_items")
    items = []
    for rec in data.get("list") or []:
        f = _fields(rec)
        items.append({
            "line": _int((rec.get("display_field") or {}).get("value")),
            "code": str(_value(f, "Name", "")).strip(),
            "description": _value(f, "Description", ""),
            "unit": _value(f, "Unit", ""),
            "quantity": _value(f, "Quantity"),
        })
    return items
