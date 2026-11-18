"""Multi-store plugin tests — fixture HTML only, NO live network.

Covers issue #2: the BaseScraper plugin system, the PriceOye and
Telemart scrapers (fixtures are trimmed excerpts of real pages captured
live), and the acceptance criterion — adding a new store requires only
a new scraper class, with no changes to the CLI, dashboard, or tracker.
"""

from pathlib import Path

import pytest

from price_tracker import database as db
from price_tracker import scraper
from price_tracker.scraper import (
    BaseScraper,
    DarazScraper,
    GenericScraper,
    PriceOyeScraper,
    ScrapeError,
    TelemartScraper,
    fetch_product_price,
    get_scraper,
    register_extractor,
    register_scraper,
    supported_stores,
)
from price_tracker.tracker import check_all

FIXTURES = Path(__file__).parent / "fixtures"
PRICEOYE_HTML = (FIXTURES / "priceoye_product.html").read_text(encoding="utf-8")
TELEMART_HTML = (FIXTURES / "telemart_product.html").read_text(encoding="utf-8")


# --- PriceOye ------------------------------------------------------------


def test_priceoye_fixture():
    name, price, currency = PriceOyeScraper.extract(PRICEOYE_HTML)
    assert name == "Samsung Galaxy A07"
    assert price == 36295.0
    assert currency == "PKR"


def test_priceoye_title_and_text_fallback():
    # No JSON-LD at all: name from <title>, price from the visible text.
    html = (
        "<html><head><title>Samsung Galaxy A07 Price in Pakistan 2026 | Priceoye</title></head>"
        "<body><p>Buy Samsung Galaxy A07 at the lowest price in Pakistan of Rs. 36,295/-.</p></body></html>"
    )
    name, price, currency = PriceOyeScraper.extract(html)
    assert name == "Samsung Galaxy A07"
    assert price == 36295.0
    assert currency == "PKR"


def test_priceoye_missing_price_raises():
    html = (
        "<html><head><title>Samsung Galaxy A07 Price in Pakistan 2026 | Priceoye</title></head>"
        "<body><p>No price anywhere on this page.</p></body></html>"
    )
    with pytest.raises(ScrapeError):
        PriceOyeScraper.extract(html)


# --- Telemart ------------------------------------------------------------


def test_telemart_fixture():
    name, price, currency = TelemartScraper.extract(TELEMART_HTML)
    assert name == "Goonj Nexo Wireless Earbuds"
    # Sale price, NOT the struck-through compare price (3,699) or the
    # stray "Rs.0" in the body text.
    assert price == 2699.0
    assert currency == "PKR"


def test_telemart_json_fallback():
    # No sale span: the embedded "price":269900 minor-units field.
    html = (
        "<html><head><title>Goonj Nexo Wireless Earbuds &ndash; TeleX (Telemart)</title></head>"
        '<body><script>var variant = {"id":123,"price":269900};</script></body></html>'
    )
    name, price, _ = TelemartScraper.extract(html)
    assert name == "Goonj Nexo Wireless Earbuds"
    assert price == 2699.0


def test_telemart_missing_price_raises():
    # No sale span and no JSON field: must raise, never return the
    # stray "Rs.0" visible on the page.
    html = (
        "<html><head><title>Goonj Nexo Wireless Earbuds &ndash; TeleX (Telemart)</title></head>"
        "<body><p>Buy now and get a Rs.0 gift card on your first order.</p></body></html>"
    )
    with pytest.raises(ScrapeError):
        TelemartScraper.extract(html)


# --- Registry / dispatch -------------------------------------------------


def test_supported_stores():
    assert supported_stores() == ["Daraz", "PriceOye", "Telemart"]


def test_get_scraper_dispatch():
    assert isinstance(get_scraper("https://www.daraz.pk/products/x.html"), DarazScraper)
    assert isinstance(
        get_scraper("https://priceoye.pk/mobiles/samsung/samsung-galaxy-a07"),
        PriceOyeScraper,
    )
    assert isinstance(get_scraper("https://telemart.pk/abc"), TelemartScraper)
    assert isinstance(
        get_scraper("https://www.telex.pk/collections/x/products/y"), TelemartScraper
    )
    assert isinstance(get_scraper("https://example-shop.com/item/1"), GenericScraper)


def test_fetch_product_price_dispatches_to_priceoye(monkeypatch):
    monkeypatch.setattr(scraper, "_download", lambda url: PRICEOYE_HTML)
    assert fetch_product_price(
        "https://priceoye.pk/mobiles/samsung/samsung-galaxy-a07"
    ) == ("Samsung Galaxy A07", 36295.0, "PKR")


def test_fetch_product_price_dispatches_to_telemart(monkeypatch):
    monkeypatch.setattr(scraper, "_download", lambda url: TELEMART_HTML)
    assert fetch_product_price(
        "https://www.telex.pk/collections/wireless-earbuds/products/goonj-nexo-wireless-earbuds"
    ) == ("Goonj Nexo Wireless Earbuds", 2699.0, "PKR")


def test_check_all_records_priceoye_price(monkeypatch, tmp_path):
    # Full pipeline for a new store: no injected fetch, no CLI/dashboard
    # involvement — the real dispatch path records the price and fires
    # the target alert.
    monkeypatch.setattr(scraper, "_download", lambda url: PRICEOYE_HTML)
    db_path = tmp_path / "priceoye.db"
    db.add_product(
        db_path,
        "https://priceoye.pk/mobiles/samsung/samsung-galaxy-a07",
        name="Galaxy A07",
        target_price=37000.0,
    )
    alerts = check_all(db_path)
    assert db.get_latest(db_path, 1).price == 36295.0
    assert any(a.kind == "target_hit" for a in alerts)


# --- Issue #2 acceptance -------------------------------------------------


def test_new_store_needs_only_a_scraper_class(monkeypatch, tmp_path):
    """Adding a store = one new class. No CLI/dashboard/tracker change."""

    @register_scraper
    class FakeStoreScraper(BaseScraper):
        name = "FakeStore"
        domains = ("fakestore.example",)

        @staticmethod
        def extract(html: str):
            return "Fake Widget", 1234.0, "PKR"

    try:
        assert "FakeStore" in supported_stores()
        monkeypatch.setattr(scraper, "_download", lambda url: "<html></html>")
        assert fetch_product_price("https://fakestore.example/p/1") == (
            "Fake Widget",
            1234.0,
            "PKR",
        )
        # End-to-end through check_all with the real dispatch (no fake fetch).
        db_path = tmp_path / "fake.db"
        db.add_product(
            db_path,
            "https://fakestore.example/p/1",
            name="Widget",
            target_price=1500.0,
        )
        alerts = check_all(db_path)
        assert db.get_latest(db_path, 1).price == 1234.0
        assert any(a.kind == "target_hit" for a in alerts)
    finally:
        scraper.SCRAPERS.remove(FakeStoreScraper)
        scraper.EXTRACTORS.pop("fakestore.example", None)


def test_legacy_register_extractor_still_works(monkeypatch):
    """The old function-based API keeps working through the new registry."""

    @register_extractor("legacy-shop.test")
    def extract_legacy(html: str):
        return "Legacy Item", 42.0, "PKR"

    monkeypatch.setattr(scraper, "_download", lambda url: "<html></html>")
    try:
        assert "legacy-shop.test" in scraper.EXTRACTORS
        assert fetch_product_price("https://legacy-shop.test/item") == (
            "Legacy Item",
            42.0,
            "PKR",
        )
    finally:
        scraper.SCRAPERS[:] = [
            cls for cls in scraper.SCRAPERS if "legacy-shop.test" not in cls.domains
        ]
        scraper.EXTRACTORS.pop("legacy-shop.test", None)
