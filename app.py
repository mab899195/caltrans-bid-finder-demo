"""Project list web page.  Run:  .venv/bin/python app.py  → http://127.0.0.1:5050"""
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, abort, jsonify, render_template, request

import alerts
import config
import db
import filters
from scraper.parse import project_links
from scraper.run import ScrapeInProgress, run_scrape

app = Flask(__name__)
log = logging.getLogger("bid_finder.app")

# A "visit" lasts this long, so the New badges survive reloads during one session.
VISIT_GAP = timedelta(minutes=30)

_scrape_thread = None


def _scraping():
    return _scrape_thread is not None and _scrape_thread.is_alive()


def _run_info(conn):
    return {
        "scraping": _scraping(),
        "last_run": db.last_run(conn),
        "last_success": db.last_run(conn, successful_only=True),
    }


@app.get("/")
def index():
    defaults = {
        "districts": config.TARGET_DISTRICTS,
        "district_names": config.DISTRICTS,
        "estimate_min": config.ESTIMATE_MIN,
        "estimate_max": config.ESTIMATE_MAX,
    }
    return render_template("index.html", defaults=defaults, statuses=db.STATUSES)


@app.post("/api/visit")
def visit():
    """Record a visit. Projects first seen after the previous visit get a New badge."""
    now = datetime.fromisoformat(db.now_iso())
    with db.connect() as conn:
        last = db.get_meta(conn, "last_visit")
        if last is None or now - datetime.fromisoformat(last) > VISIT_GAP:
            db.set_meta(conn, "previous_visit", last or "")
        db.set_meta(conn, "last_visit", now.isoformat())
        previous = db.get_meta(conn, "previous_visit") or None
    return jsonify({"previous_visit": previous})


@app.get("/api/projects")
def projects():
    with db.connect() as conn:
        previous = db.get_meta(conn, "previous_visit") or None
        rows = db.list_projects(conn)
        for p in rows:
            p["links"] = project_links(p["contract"])
            p["is_new"] = previous is None or p["first_seen"] > previous
            for i in p["electrical_items"] or []:
                i["minor"] = i["code"] in config.MINOR_ELECTRICAL_ITEMS
            p["is_electrical"] = filters.has_electrical_scope(p["keyword_hits"], p["electrical_items"])
        return jsonify({"projects": rows, "previous_visit": previous, **_run_info(conn)})


@app.post("/api/projects/<contract>/status")
def set_status(contract):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in db.STATUSES:
        return jsonify({"error": f"status must be one of {db.STATUSES}"}), 400
    with db.connect() as conn:
        if not db.set_status(conn, contract, status):
            return jsonify({"error": "unknown contract"}), 404
    return jsonify({"contract": contract, "status": status})


def _refresh_blocked():
    """Why a manual scrape can't start now, or None. Keeps a public deployment
    from hitting Caltrans every time someone clicks Refresh."""
    if _scraping():
        return "A scrape is already running."
    with db.connect() as conn:
        last = db.last_run(conn, finished=False)
    if not last:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(last["started_at"])
    if not last["finished_at"] and age < timedelta(minutes=5):
        return "A scrape is already running."
    wait = timedelta(minutes=config.REFRESH_COOLDOWN_MINUTES) - age
    if wait > timedelta(0):
        return f"Data is fresh; refresh again in {int(wait.total_seconds() // 60) + 1} min."
    return None


def _scrape_logged(trigger):
    try:
        return run_scrape(trigger=trigger)
    except ScrapeInProgress:
        log.info("skipped: another scrape is running")
    except Exception:
        pass  # recorded in scrape_runs; the page shows it


@app.post("/api/refresh")
def refresh():
    global _scrape_thread
    reason = _refresh_blocked()
    if reason:
        return jsonify({"error": reason}), 429
    if config.ON_VERCEL:
        # No background threads on serverless: scrape within the request.
        _scrape_logged("manual")
        return jsonify({"started": True, "finished": True})
    _scrape_thread = threading.Thread(target=_scrape_logged, args=("manual",), daemon=True)
    _scrape_thread.start()
    return jsonify({"started": True}), 202


@app.get("/api/cron/scrape")
def cron_scrape():
    """Daily scrape, called by Vercel Cron (see vercel.json)."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or request.headers.get("Authorization") != f"Bearer {secret}":
        abort(401)
    return jsonify(_scrape_logged("scheduled") or {"error": "scrape failed; see scrape_runs"})


@app.route("/api/alert-settings", methods=["GET", "POST"])
def alert_settings():
    with db.connect() as conn:
        queued = 0
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            try:
                alerts.save_settings(conn, body)
            except (TypeError, ValueError):
                return jsonify({"error": "invalid alert settings"}), 400
            queued = alerts.queue_new_alerts(conn)  # no network: uses stored projects
        return jsonify({"settings": alerts.get_settings(conn), "queued": queued,
                        "alert_count": len(db.list_alerts(conn)), "mode": config.ALERT_MODE})


@app.get("/alerts")
def alerts_page():
    return render_template("alerts.html", mode=config.ALERT_MODE)


@app.get("/api/alerts")
def alert_list():
    with db.connect() as conn:
        return jsonify({"alerts": db.list_alerts(conn), "mode": config.ALERT_MODE})


@app.get("/alerts/<int:alert_id>/email")
def alert_email(alert_id):
    with db.connect() as conn:
        a = db.get_alert(conn, alert_id)
    if not a:
        abort(404)
    return a["body_html"]


@app.get("/alerts/preview/<contract>")
def alert_preview(contract):
    """The email a project would produce, whether or not it matches the filter."""
    with db.connect() as conn:
        p = next((x for x in db.list_projects(conn, include_inactive=True)
                  if x["contract"] == contract), None)
    if not p:
        abort(404)
    return alerts.build_email(p)[2]


@app.get("/results")
def results_page():
    defaults = {
        "districts": config.TARGET_DISTRICTS,
        "district_names": config.DISTRICTS,
        "minor_items": config.MINOR_ELECTRICAL_ITEMS,
        "company": config.COMPANY,
    }
    return render_template("results.html", defaults=defaults)


@app.get("/api/bid-results")
def bid_results():
    with db.connect() as conn:
        rows, unreadable = db.list_bid_results(conn)
        known = {p["contract"]: p for p in db.list_projects(conn, include_inactive=True)}
    for r in rows:
        # Prefer the description we saw while the project was advertised.
        p = known.get(r["contract"])
        if p and p["description"]:
            r["description"] = p["description"]
        r["links"] = {**project_links(r["contract"]),
                      "summary_pdf": f"{config.ATTACHMENT_API}/{r['summary_sys_id']}"}
    unreadable = [{"contract": u["contract"], "district": u["district"], "bid_open": u["bid_open"],
                   "reason": "scanned" if (u["error"] or "").startswith("scanned") else "unreadable",
                   "summary_pdf": f"{config.ATTACHMENT_API}/{u['summary_sys_id']}"} for u in unreadable]
    return jsonify({"results": rows, "unreadable": unreadable})


@app.get("/api/status")
def status():
    with db.connect() as conn:
        return jsonify(_run_info(conn))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app.run(host="127.0.0.1", port=5050, debug=False)
