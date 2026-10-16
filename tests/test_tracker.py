"""Tracker alert-logic tests with a fake scraper (no network)."""

import pytest

from price_tracker import database as db
from price_tracker.alerts import Alert, format_alert
from price_tracker.scraper import ScrapeError
from price_tracker.tracker import check_all


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def _fake_fetch(prices: dict):
    def fetch(url: str):
        if url not in prices:
            raise ScrapeError("boom")
        return ("Fake Product", prices[url], "PKR")

    return fetch


def test_first_check_raises_no_drop_alert(db_path):
    db.add_product(db_path, "https://x.test/a", name="A")
    alerts = check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 1000.0}))
    assert alerts == []
    assert db.get_latest(db_path, 1).price == 1000.0


def test_price_drop_alert(db_path):
    db.add_product(db_path, "https://x.test/a", name="A")
    check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 1000.0}))
    alerts = check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 850.0}))
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.kind == "price_drop"
    assert alert.old_price == 1000.0
    assert alert.new_price == 850.0
    assert alert.drop_pct == 15.0


def test_price_rise_no_alert(db_path):
    db.add_product(db_path, "https://x.test/a", name="A")
    check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 1000.0}))
    alerts = check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 1100.0}))
    assert alerts == []


def test_target_hit_alert(db_path):
    db.add_product(db_path, "https://x.test/a", name="A", target_price=900.0)
    alerts = check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 899.0}))
    kinds = [a.kind for a in alerts]
    assert "target_hit" in kinds  # first check can still hit the target


def test_drop_and_target_together(db_path):
    db.add_product(db_path, "https://x.test/a", name="A", target_price=900.0)
    check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 1000.0}))
    alerts = check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 850.0}))
    assert {a.kind for a in alerts} == {"price_drop", "target_hit"}


def test_failed_product_does_not_abort_round(db_path):
    db.add_product(db_path, "https://x.test/a", name="A")
    db.add_product(db_path, "https://x.test/b", name="B")
    alerts = check_all(db_path, fetch=_fake_fetch({"https://x.test/b": 500.0}))
    assert alerts == []  # A failed, B recorded fine
    assert db.get_latest(db_path, 1) is None
    assert db.get_latest(db_path, 2).price == 500.0


def test_scraper_name_updates_product(db_path):
    db.add_product(db_path, "https://x.test/a", name="https://x.test/a")
    check_all(db_path, fetch=_fake_fetch({"https://x.test/a": 100.0}))
    assert db.get_product(db_path, 1).name == "Fake Product"


def test_format_alert():
    a = Alert(1, "Earbuds", "price_drop", 1000.0, 850.0, "PKR")
    text = format_alert(a)
    assert "Earbuds" in text and "15.0%" in text
    t = Alert(1, "Earbuds", "target_hit", 1000.0, 850.0, "PKR")
    assert "target" in format_alert(t).lower()
