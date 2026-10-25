import pytest

import app as app_module
import config
import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    calls = []
    monkeypatch.setattr(app_module, "run_scrape", lambda trigger: calls.append(trigger) or {"ok": 1})
    c = app_module.app.test_client()
    c.calls = calls
    return c


def test_cron_requires_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    assert client.get("/api/cron/scrape").status_code == 401
    assert client.get("/api/cron/scrape", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/api/cron/scrape", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and client.calls == ["scheduled"]


def test_cron_disabled_without_secret(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/api/cron/scrape", headers={"Authorization": "Bearer "}).status_code == 401


def test_refresh_cooldown_on_vercel(client, monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", True)
    monkeypatch.setattr(config, "REFRESH_COOLDOWN_MINUTES", 15)
    with db.connect() as conn:                       # a scrape finished just now
        run_id = db.start_run(conn, "scheduled")
        db.finish_run(conn, run_id, projects_found=1)
    r = client.post("/api/refresh")
    assert r.status_code == 429 and "refresh again" in r.get_json()["error"]
    assert client.calls == []


def test_refresh_runs_inline_on_vercel(client, monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", True)
    r = client.post("/api/refresh")
    assert r.status_code == 200 and r.get_json()["finished"] and client.calls == ["manual"]


def test_pages_render(client):
    for path in ("/", "/results", "/alerts"):
        assert client.get(path).status_code == 200
    assert client.get("/api/bid-results").get_json() == {"results": [], "unreadable": []}
