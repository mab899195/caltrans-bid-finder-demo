"""Storage: every project seen, bid results, scrape history, alerts and app state.

Uses Postgres when DATABASE_URL is set (the Vercel deployment) and SQLite
otherwise (local runs and tests). Queries are written once, SQLite style
(``?`` and ``:name`` placeholders); the Postgres wrapper translates them.
"""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

STATUSES = ("new", "reviewing", "bidding", "passed")
STAGES = ("upcoming", "advertised")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    contract          TEXT PRIMARY KEY,
    district          TEXT NOT NULL,
    sys_id            TEXT,
    description       TEXT,
    county_route_pm   TEXT,
    county            TEXT,
    location          TEXT,
    advertised        TEXT,
    bid_open          TEXT,
    estimate          BIGINT,
    working_days      INTEGER,
    goal              TEXT,
    licenses          TEXT,
    mandatory_prebid  INTEGER NOT NULL DEFAULT 0,
    postponed         INTEGER NOT NULL DEFAULT 0,
    addenda_count     INTEGER NOT NULL DEFAULT 0,
    inquiry_count     INTEGER NOT NULL DEFAULT 0,
    keyword_hits      TEXT NOT NULL DEFAULT '[]',
    electrical_items  TEXT,              -- JSON list; NULL = bid items not checked
    bid_items_count   INTEGER,
    bid_items_checked_at      TEXT,
    bid_items_checked_addenda INTEGER,
    status            TEXT NOT NULL DEFAULT 'new',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1,  -- on the advertised or upcoming list
    stage             TEXT NOT NULL DEFAULT 'advertised'
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    trigger         TEXT,
    projects_found  INTEGER,
    new_projects    INTEGER,
    bid_items_fetched INTEGER,
    error           TEXT,
    warning         TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- One row per project ever alerted on; UNIQUE(contract) is the dedup guard.
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contract    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    recipient   TEXT,
    subject     TEXT NOT NULL,
    body_text   TEXT NOT NULL,
    body_html   TEXT NOT NULL,
    status      TEXT NOT NULL      -- 'demo' = saved to the outbox, not sent
);
-- Parsed Bid Summary PDFs. ``data`` holds the parsed JSON (bidders, amounts...).
CREATE TABLE IF NOT EXISTS bid_results (
    contract        TEXT PRIMARY KEY,
    district        TEXT NOT NULL,
    bid_open        TEXT,
    summary_sys_id  TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    data            TEXT,
    error           TEXT,
    parser_version  INTEGER NOT NULL DEFAULT 1
);
"""

# Columns added after the first release, for databases created before them.
MIGRATIONS = [
    ("projects", "stage", "TEXT NOT NULL DEFAULT 'advertised'"),
    ("bid_results", "parser_version", "INTEGER NOT NULL DEFAULT 1"),
]

PROJECT_FIELDS = (
    "district", "sys_id", "description", "county_route_pm", "county", "location",
    "advertised", "bid_open", "estimate", "working_days", "goal", "licenses",
    "mandatory_prebid", "postponed", "addenda_count", "inquiry_count", "keyword_hits",
    "stage",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_url():
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")


class PostgresConnection:
    """Gives a psycopg connection the sqlite3 API this module uses."""
    _named = re.compile(r"(?<!:):(\w+)")

    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row
        self._conn = psycopg.connect(url, row_factory=dict_row)

    def execute(self, sql, params=()):
        sql = self._named.sub(r"%(\1)s", sql).replace("?", "%s")
        return self._conn.execute(sql, params)

    def executescript(self, script):
        self._conn.execute(script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"))

    def columns(self, table):
        q = "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
        return {r["column_name"] for r in self._conn.execute(q, (table,))}

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class SqliteConnection(sqlite3.Connection):
    def columns(self, table):
        return {r[1] for r in self.execute(f"PRAGMA table_info({table})")}


@contextmanager
def connect(path=None):
    url = None if path else database_url()
    if url:
        conn = PostgresConnection(url)
    else:
        path = path or config.DB_PATH
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, factory=SqliteConnection, timeout=15)
        conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        if column not in conn.columns(table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _placeholders(values):
    return ",".join("?" * len(values))


# --- projects -----------------------------------------------------------------

def upsert_projects(conn, projects, seen_at):
    """Insert or update projects from one scrape. Returns new contract numbers.
    Projects missing from this scrape are marked inactive."""
    existing = {r["contract"] for r in conn.execute("SELECT contract FROM projects")}
    new = []
    for p in projects:
        row = {k: p.get(k) for k in PROJECT_FIELDS}
        row["keyword_hits"] = json.dumps(p.get("keyword_hits", []))
        row["mandatory_prebid"] = int(bool(p.get("mandatory_prebid")))
        row["postponed"] = int(bool(p.get("postponed")))
        row["stage"] = p.get("stage") or "advertised"
        if p["contract"] in existing:
            sets = ", ".join(f"{k} = :{k}" for k in row)
            conn.execute(
                f"UPDATE projects SET {sets}, last_seen = :seen, active = 1 WHERE contract = :contract",
                {**row, "seen": seen_at, "contract": p["contract"]},
            )
        else:
            cols = ", ".join(row)
            vals = ", ".join(f":{k}" for k in row)
            conn.execute(
                f"INSERT INTO projects (contract, {cols}, first_seen, last_seen) "
                f"VALUES (:contract, {vals}, :seen, :seen)",
                {**row, "seen": seen_at, "contract": p["contract"]},
            )
            new.append(p["contract"])
    current = [p["contract"] for p in projects]
    conn.execute(
        f"UPDATE projects SET active = 0 WHERE contract NOT IN ({_placeholders(current)})", current
    )
    return new


def projects_needing_bid_items(conn, districts):
    """Active projects in the given districts never checked, or whose addenda
    count changed since the last check (an addendum can change bid items)."""
    q = (
        "SELECT contract FROM projects WHERE active = 1 "
        f"AND district IN ({_placeholders(districts)}) "
        "AND (bid_items_checked_at IS NULL OR bid_items_checked_addenda != addenda_count) "
        "ORDER BY contract"
    )
    return [r["contract"] for r in conn.execute(q, list(districts))]


def save_bid_items(conn, contract, all_items, electrical, checked_at):
    conn.execute(
        "UPDATE projects SET electrical_items = ?, bid_items_count = ?, "
        "bid_items_checked_at = ?, bid_items_checked_addenda = addenda_count "
        "WHERE contract = ?",
        (json.dumps(electrical), len(all_items), checked_at, contract),
    )


def list_projects(conn, include_inactive=False):
    q = "SELECT * FROM projects" + ("" if include_inactive else " WHERE active = 1")
    out = []
    for r in conn.execute(q):
        d = dict(r)
        d["keyword_hits"] = json.loads(d["keyword_hits"] or "[]")
        d["electrical_items"] = (
            json.loads(d["electrical_items"]) if d["electrical_items"] is not None else None
        )
        d["mandatory_prebid"] = bool(d["mandatory_prebid"])
        d["postponed"] = bool(d["postponed"])
        d["active"] = bool(d["active"])
        out.append(d)
    return out


def set_status(conn, contract, status):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    cur = conn.execute("UPDATE projects SET status = ? WHERE contract = ?", (status, contract))
    return cur.rowcount == 1


# --- bid results --------------------------------------------------------------

def bid_results_done(conn, parser_version):
    """contract -> summary sys_id for results that need no refetch: parsed fine,
    or failed under the current parser version."""
    q = "SELECT contract, summary_sys_id FROM bid_results WHERE error IS NULL OR parser_version >= ?"
    return {r["contract"]: r["summary_sys_id"] for r in conn.execute(q, (parser_version,))}


def save_bid_result(conn, contract, bid_open, summary_sys_id, parser_version, data=None, error=None):
    conn.execute("DELETE FROM bid_results WHERE contract = ?", (contract,))
    conn.execute(
        "INSERT INTO bid_results (contract, district, bid_open, summary_sys_id, fetched_at, data, error, "
        "parser_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (contract, contract[:2], bid_open, summary_sys_id, now_iso(),
         json.dumps(data) if data is not None else None, error, parser_version),
    )


def list_bid_results(conn):
    """Parsed results, plus ``unreadable``: summaries that couldn't be parsed."""
    out, unreadable = [], []
    for r in conn.execute("SELECT * FROM bid_results ORDER BY bid_open DESC, contract"):
        d = dict(r)
        data = d.pop("data")
        if data is None:
            unreadable.append(d)
        else:
            d.update(json.loads(data))
            out.append(d)
    return out, unreadable


# --- scrape runs --------------------------------------------------------------

def start_run(conn, trigger):
    r = conn.execute(
        "INSERT INTO scrape_runs (started_at, trigger) VALUES (?, ?) RETURNING id", (now_iso(), trigger)
    ).fetchone()
    return r["id"]


def finish_run(conn, run_id, **fields):
    fields["finished_at"] = now_iso()
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    conn.execute(f"UPDATE scrape_runs SET {sets} WHERE id = :id", {**fields, "id": run_id})


def last_run(conn, successful_only=False, finished=True):
    q = "SELECT * FROM scrape_runs WHERE 1 = 1"
    if finished:
        q += " AND finished_at IS NOT NULL"
    if successful_only:
        q += " AND error IS NULL"
    r = conn.execute(q + " ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


# --- meta ---------------------------------------------------------------------

def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- alerts -------------------------------------------------------------------

def alerted_contracts(conn):
    return {r["contract"] for r in conn.execute("SELECT contract FROM alerts")}


def insert_alert(conn, contract, recipient, subject, body_text, body_html, status):
    conn.execute(
        "INSERT INTO alerts (contract, created_at, recipient, subject, body_text, body_html, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (contract, now_iso(), recipient, subject, body_text, body_html, status),
    )


def list_alerts(conn):
    q = "SELECT id, contract, created_at, recipient, subject, status FROM alerts ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q)]


def get_alert(conn, alert_id):
    r = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return dict(r) if r else None
