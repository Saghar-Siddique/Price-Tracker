"""Database round-trip tests (isolated tmp SQLite files)."""

import sqlite3

import pytest

from price_tracker import database as db


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def test_add_and_list_products(db_path):
    p1 = db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A", target_price=100.0)
    p2 = db.add_product(db_path, "https://www.daraz.pk/products/b.html")
    assert p1.id is not None and p2.id is not None
    assert p1.id != p2.id
    products = db.list_products(db_path)
    assert [p.name for p in products] == ["A", "https://www.daraz.pk/products/b.html"]
    assert products[0].target_price == 100.0
    assert products[1].target_price is None


def test_add_duplicate_url_raises(db_path):
    db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A2")


def test_record_and_get_history(db_path):
    p = db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A")
    db.record_price(db_path, p.id, 500.0, "PKR")
    db.record_price(db_path, p.id, 450.0, "PKR")
    history = db.get_history(db_path, p.id)
    assert [h.price for h in history] == [500.0, 450.0]
    latest = db.get_latest(db_path, p.id)
    assert latest is not None and latest.price == 450.0
    assert latest.currency == "PKR"


def test_get_latest_empty(db_path):
    p = db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A")
    assert db.get_latest(db_path, p.id) is None


def test_remove_product_cascades_history(db_path):
    p = db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A")
    db.record_price(db_path, p.id, 500.0)
    assert db.remove_product(db_path, p.id) is True
    assert db.list_products(db_path) == []
    assert db.get_history(db_path, p.id) == []
    assert db.remove_product(db_path, p.id) is False


def test_get_product(db_path):
    p = db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="A")
    fetched = db.get_product(db_path, p.id)
    assert fetched is not None and fetched.url.endswith("a.html")
    assert db.get_product(db_path, 9999) is None


def test_update_product_name(db_path):
    p = db.add_product(db_path, "https://www.daraz.pk/products/a.html", name="old")
    db.update_product_name(db_path, p.id, "Real Product Name")
    assert db.get_product(db_path, p.id).name == "Real Product Name"
