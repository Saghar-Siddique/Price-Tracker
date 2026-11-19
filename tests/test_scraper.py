"""Scraper tests — fixture HTML only, NO live network."""

import os
from pathlib import Path

import pytest

from price_tracker import scraper
from price_tracker.scraper import (
    EXTRACTORS,
    ScrapeError,
    extract_daraz,
    extract_generic,
    fetch_product_price,
    parse_price,
)

FIXTURE = Path(__file__).parent / "fixtures" / "daraz_product.html"


def test_parse_price_formats():
    assert parse_price("Rs. 159,999") == 159999.0
    assert parse_price("Rs.495") == 495.0
    assert parse_price("PKR 1,299.50") == 1299.50
    with pytest.raises(ScrapeError):
        parse_price("no digits here")


def test_extract_daraz_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    name, price, currency = extract_daraz(html)
    assert name == "Sensodyne Original Toothpaste 100gm"
    assert price == 495.0
    assert currency == "PKR"


def test_extract_daraz_missing_price_raises():
    html = "<html><head><title>Widget | Daraz.pk</title></head><body>no price</body></html>"
    with pytest.raises(ScrapeError):
        extract_daraz(html)


def test_extract_daraz_json_ld_name_fallback():
    # pdpTrackingData present but name missing -> JSON-LD name used
    html = (
        '<html><head><title>Fallback Page</title>'
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"LD Name",'
        '"offers":{"@type":"Offer","price":"1234"}}'
        "</script></head><body></body></html>"
    )
    name, price, _ = extract_daraz(html)
    assert name == "LD Name"
    assert price == 1234.0


def test_registry_dispatch_uses_daraz_extractor(monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(scraper, "_download", lambda url: html)

    def no_browser(url, timeout_ms=30000):
        raise ScrapeError("no browser in tests")

    # force the static fallback path (no real Chromium in tests)
    monkeypatch.setattr(scraper, "fetch_rendered_price", no_browser)
    name, price, currency = fetch_product_price(
        "https://www.daraz.pk/products/some-thing-i123-s456.html"
    )
    assert (name, price, currency) == (
        "Sensodyne Original Toothpaste 100gm",
        495.0,
        "PKR",
    )


def test_fetch_product_price_prefers_rendered(monkeypatch):
    # Playwright path returns the exact (possibly promotion) price
    monkeypatch.setattr(
        scraper,
        "fetch_rendered_price",
        lambda url, timeout_ms=30000: ("Promo Earphones", 234.0, "PKR"),
    )
    name, price, currency = fetch_product_price(
        "https://www.daraz.pk/products/x-i1-s2.html"
    )
    assert (name, price, currency) == ("Promo Earphones", 234.0, "PKR")


def test_fetch_product_price_falls_back_to_static(monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")

    def no_browser(url, timeout_ms=30000):
        raise ScrapeError("no browser")

    monkeypatch.setattr(scraper, "fetch_rendered_price", no_browser)
    monkeypatch.setattr(scraper, "_download", lambda url: html)
    name, price, currency = fetch_product_price(
        "https://www.daraz.pk/products/x-i1-s2.html"
    )
    assert price == 495.0


def test_fetch_rendered_price_without_playwright(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ScrapeError, match="playwright is not installed"):
        scraper.fetch_rendered_price("https://www.daraz.pk/products/x-i1-s2.html")


def test_browser_proxy_none_without_env(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    assert scraper._browser_proxy() is None


def test_registry_dispatch_unknown_domain_uses_generic(monkeypatch):
    html = (
        "<html><head>"
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Generic Gadget",'
        '"offers":{"@type":"Offer","price":"999.99","priceCurrency":"USD"}}'
        "</script></head><body></body></html>"
    )
    monkeypatch.setattr(scraper, "_download", lambda url: html)
    name, price, currency = fetch_product_price("https://example-shop.com/item/1")
    assert name == "Generic Gadget"
    assert price == 999.99
    assert currency == "USD"


def test_extract_generic_no_data_raises():
    with pytest.raises(ScrapeError):
        extract_generic("<html><body>nothing here</body></html>")


def test_daraz_registered():
    assert "daraz.pk" in EXTRACTORS


# ---------------------------------------------------------------------------
# Sale-page regression (live-app bug found Nov 19, 2026)
# ---------------------------------------------------------------------------

#: Trimmed real Daraz page for a product on sale (AirBuds_Pro): the page
#: sells at Rs. 699 with Rs. 2,199 struck through as the original price.
SALE_FIXTURE = Path(__file__).parent / "fixtures" / "daraz_sale_product.html"
SALE_URL = (
    "https://www.daraz.pk/products/airbuds_pro-wireless-earbuds-"
    "bluetooth-50-super-sound-bass-charging-case-and-extra-ear-buds-"
    "pop-up-feature-compatible-with-all-devices-i1983435427-s14073966757.html"
)


def test_daraz_static_returns_list_price_on_sale_page():
    """Static extraction on a sale page returns the struck-through LIST
    price (Rs. 2,199) — the Rs. 699 sale price never appears in the
    static HTML. Pinned so this limitation stays visible: the static
    path is a degraded fallback, never the source of truth for a price."""
    html = SALE_FIXTURE.read_text(encoding="utf-8")
    name, price, currency = extract_daraz(html)
    assert "AirBuds_Pro" in name
    assert price == 2199.0
    assert currency == "PKR"


def test_daraz_fetch_returns_list_price_when_browser_missing(monkeypatch):
    """Mirrors the live failure exactly: with no browser available, the
    Daraz fetch falls back to static HTML and records the list price."""
    def no_browser(url, timeout_ms=30000):
        raise ScrapeError("no browser")

    monkeypatch.setattr(scraper, "fetch_rendered_price", no_browser)
    monkeypatch.setattr(
        scraper, "_download", lambda url: SALE_FIXTURE.read_text(encoding="utf-8")
    )
    name, price, currency = scraper.DarazScraper().fetch(SALE_URL)
    assert price == 2199.0
    assert currency == "PKR"


def test_launch_browser_falls_back_to_system_chromium(monkeypatch):
    """When the bundled browser can't launch, a system Chromium is tried."""
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_PATH", raising=False)
    calls = []

    class FakeChromium:
        def launch(self, **kwargs):
            calls.append(kwargs)
            if "executable_path" not in kwargs:
                raise Exception("Executable doesn't exist")
            return "fake-browser"

    class FakeP:
        chromium = FakeChromium()

    monkeypatch.setattr(os.path, "exists", lambda p: p == "/usr/bin/chromium")
    assert scraper._launch_browser(FakeP()) == "fake-browser"
    assert calls[0].get("executable_path") is None  # bundled tried first
    assert calls[1]["executable_path"] == "/usr/bin/chromium"
    assert calls[0]["args"] == ["--no-sandbox"]


def test_launch_browser_raises_when_nothing_works(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_PATH", raising=False)

    class FakeChromium:
        def launch(self, **kwargs):
            raise Exception("no browser at all")

    class FakeP:
        chromium = FakeChromium()

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    with pytest.raises(ScrapeError, match="Could not launch Chromium"):
        scraper._launch_browser(FakeP())
