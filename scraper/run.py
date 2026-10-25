"""One scrape: fetch the advertised list, store it, then check bid items for
target-district projects that need it.

Run from the command line (used by the daily launchd job):
    .venv/bin/python -m scraper.run
"""
import logging
import sys
import threading
import time

import alerts
import config
import db
import filters
from scraper.bid_summary import PARSER_VERSION, ScannedPdf, parse_summary_list, parse_summary_pdf
from scraper.fetch import Fetcher
from scraper.parse import parse_advertisements, parse_bid_items

log = logging.getLogger("bid_finder.scrape")

# Only one scrape at a time (the Refresh button and the daily job share this).
_lock = threading.Lock()


class ScrapeInProgress(Exception):
    pass


def run_scrape(trigger="manual", fetcher=None, db_path=None):
    if not _lock.acquire(blocking=False):
        raise ScrapeInProgress()
    try:
        return _run(trigger, fetcher or Fetcher(), db_path)
    finally:
        _lock.release()


def _run(trigger, fetcher, db_path):
    with db.connect(db_path) as conn:
        run_id = db.start_run(conn, trigger)
        conn.commit()
        summary = {"projects_found": 0, "new_projects": 0, "bid_items_fetched": 0}
        warnings = []
        deadline = time.monotonic() + config.SCRAPE_TIME_BUDGET_SECONDS

        def out_of_time(what):
            if time.monotonic() < deadline:
                return False
            warnings.append(f"time budget reached; remaining {what} will be fetched next run")
            return True

        try:
            projects, meta = parse_advertisements(fetcher.advertisements())
            if not projects:
                raise RuntimeError("the advertised list came back empty; not touching stored data")
            if meta.get("max_count") and len(projects) >= meta["max_count"]:
                warnings.append(
                    f"list returned {len(projects)} projects, the site's cap; some may be missing"
                )
            for p in projects:
                p["stage"] = "advertised"

            # Scheduled, not yet advertised (early warning). Not fatal if it fails.
            try:
                upcoming, _ = parse_advertisements(fetcher.upcoming())
            except Exception as e:
                upcoming = []
                warnings.append(f"upcoming list: {e}")
            advertised = {p["contract"] for p in projects}
            projects += [{**p, "stage": "upcoming"} for p in upcoming if p["contract"] not in advertised]

            for p in projects:
                p["keyword_hits"] = filters.keyword_hits(p["description"])

            seen_at = db.now_iso()
            new = db.upsert_projects(conn, projects, seen_at)
            conn.commit()
            summary.update(projects_found=len(projects), new_projects=len(new))
            log.info("found %d projects, %d new", len(projects), len(new))

            for contract in db.projects_needing_bid_items(conn, config.BID_ITEM_DISTRICTS):
                if out_of_time("bid items"):
                    break
                try:
                    items = parse_bid_items(fetcher.project_details(contract))
                except Exception as e:  # one bad project shouldn't sink the run
                    warnings.append(f"bid items for {contract}: {e}")
                    log.warning("bid items for %s failed: %s", contract, e)
                    continue
                db.save_bid_items(conn, contract, items, filters.electrical_items(items), db.now_iso())
                conn.commit()
                summary["bid_items_fetched"] += 1

            queued = alerts.queue_new_alerts(conn)
            conn.commit()
            log.info("queued %d alert(s)", queued)

            results = _fetch_bid_results(conn, fetcher, warnings, out_of_time)
            log.info("stored %d bid result(s)", results)

            db.finish_run(conn, run_id, **summary, warning="; ".join(warnings) or None)
            summary.update(alerts_queued=queued, bid_results_fetched=results)
        except Exception as e:
            log.exception("scrape failed")
            db.finish_run(conn, run_id, **summary, error=str(e),
                          warning="; ".join(warnings) or None)
            conn.commit()
            raise
    return {**summary, "warnings": warnings}


def _fetch_bid_results(conn, fetcher, warnings, out_of_time):
    """Download and parse Bid Summary PDFs not stored yet (or revised since)."""
    try:
        entries = parse_summary_list(fetcher.bid_summaries())
    except Exception as e:
        warnings.append(f"bid summary list: {e}")
        return 0
    stored = db.bid_results_done(conn, PARSER_VERSION)
    count = 0
    for e in entries:
        if stored.get(e["contract"]) == e["sys_id"]:
            continue
        if out_of_time("bid results"):
            break
        save = lambda **kw: db.save_bid_result(conn, e["contract"], e["bid_open"], e["sys_id"],
                                               PARSER_VERSION, **kw)
        try:
            save(data=parse_summary_pdf(fetcher.attachment(e["sys_id"])))
            count += 1
        except ScannedPdf as ex:  # expected now and then; not a warning
            log.info("bid summary for %s: %s", e["contract"], ex)
            save(error=str(ex))
        except Exception as ex:  # keep the error so we don't refetch a bad PDF daily
            warnings.append(f"bid summary for {e['contract']}: {ex}")
            log.warning("bid summary for %s failed: %s", e["contract"], ex)
            save(error=str(ex))
        conn.commit()
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run_scrape(trigger=sys.argv[1] if len(sys.argv) > 1 else "scheduled")
    except Exception:
        sys.exit(1)
    print(result)
