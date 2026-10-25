"""Phase 2: one email per new project that matches the saved alert filter.

In demo mode (config.ALERT_MODE) the email is built and saved to the outbox
(the ``alerts`` table), which the app shows, instead of being sent.
"""
import json
from datetime import date

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
import db
import filters
from scraper.parse import project_links

_env = Environment(
    loader=FileSystemLoader(config.BASE_DIR / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
)


def default_settings():
    return {
        "districts": list(config.TARGET_DISTRICTS),
        "estimate_min": config.ESTIMATE_MIN,
        "estimate_max": config.ESTIMATE_MAX,
        "electrical_only": True,
    }


def get_settings(conn):
    saved = db.get_meta(conn, "alert_settings")
    return {**default_settings(), **json.loads(saved)} if saved else default_settings()


def save_settings(conn, settings):
    s = {**default_settings(), **settings}
    s["districts"] = sorted({d for d in s["districts"] if d in config.DISTRICTS})
    for k in ("estimate_min", "estimate_max"):
        s[k] = None if s[k] in (None, "") else int(s[k])
    s["electrical_only"] = bool(s["electrical_only"])
    db.set_meta(conn, "alert_settings", json.dumps(s))
    return s


def matches(project, settings, today=None):
    """Whether a stored project should trigger an alert under these settings."""
    p, s = project, settings
    today = today or date.today().isoformat()
    if p["electrical_items"] is None:
        return False  # wait until bid items are checked, so the email can list them
    if p["district"] not in s["districts"] or p["estimate"] is None:
        return False
    if s["estimate_min"] is not None and p["estimate"] < s["estimate_min"]:
        return False
    if s["estimate_max"] is not None and p["estimate"] > s["estimate_max"]:
        return False
    if s["electrical_only"] and not filters.has_electrical_scope(p["keyword_hits"], p["electrical_items"]):
        return False
    return not p["bid_open"] or p["bid_open"] >= today


def build_email(project, today=None):
    """Return (subject, text, html) for one project, per PRD section 4."""
    p = project
    today = today or date.today()
    days_left = (date.fromisoformat(p["bid_open"]) - today).days if p["bid_open"] else None
    items = p["electrical_items"] or []
    ctx = {
        "p": p,
        "estimate": f"${p['estimate']:,}" if p["estimate"] is not None else "not listed",
        "days_left": days_left,
        "district_name": config.DISTRICTS.get(p["district"], ""),
        "major_items": [i for i in items if i["code"] not in config.MINOR_ELECTRICAL_ITEMS],
        "minor_items": [i for i in items if i["code"] in config.MINOR_ELECTRICAL_ITEMS],
        "links": project_links(p["contract"]),
        "app_url": config.APP_URL,
    }
    description = p["description"].rstrip(".")
    kind = "Upcoming" if p.get("stage") == "upcoming" else "New"
    subject = f"{kind} Caltrans bid – {p['contract']} – {description} – {ctx['estimate']}"
    return (
        subject,
        _env.get_template("alert.txt").render(**ctx),
        _env.get_template("alert.html").render(subject=subject, **ctx),
    )


def queue_new_alerts(conn, today=None):
    """Create one alert per matching project not alerted before. Returns how many."""
    settings = get_settings(conn)
    done = db.alerted_contracts(conn)
    count = 0
    for p in sorted(db.list_projects(conn), key=lambda p: p["first_seen"]):
        if p["contract"] in done or not matches(p, settings, today and today.isoformat()):
            continue
        subject, text, html = build_email(p, today)
        db.insert_alert(conn, p["contract"], config.ALERT_RECIPIENT, subject, text, html,
                        status=config.ALERT_MODE)
        count += 1
    return count
