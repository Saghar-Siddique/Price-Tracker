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
    name, price, currency, meta = fetch_product_price(
        "https://www.daraz.pk/products/some-thing-i123-s456.html"
    )
    assert (name, price, currency) == (
        "Sensodyne Original Toothpaste 100gm",
        495.0,
        "PKR",
    )
    assert meta["rendered"] is False
    assert "no browser in tests" in meta["fallback_reason"]


def test_fetch_product_price_prefers_rendered(monkeypatch):
    # Playwright path returns the exact (possibly promotion) price
    monkeypatch.setattr(
        scraper,
        "fetch_rendered_price",
        lambda url, timeout_ms=30000: ("Promo Earphones", 234.0, "PKR"),
    )
    name, price, currency, meta = fetch_product_price(
        "https://www.daraz.pk/products/x-i1-s2.html"
    )
    assert (name, price, currency) == ("Promo Earphones", 234.0, "PKR")
    assert meta == {"rendered": True}


def test_fetch_product_price_falls_back_to_static(monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")

    def no_browser(url, timeout_ms=30000):
        raise ScrapeError("no browser")

    monkeypatch.setattr(scraper, "fetch_rendered_price", no_browser)
    monkeypatch.setattr(scraper, "_download", lambda url: html)
    name, price, currency, meta = fetch_product_price(
        "https://www.daraz.pk/products/x-i1-s2.html"
    )
    assert price == 495.0
    assert meta["rendered"] is False
    assert meta["fallback_reason"] == "no browser"


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
    name, price, currency, meta = scraper.DarazScraper().fetch(SALE_URL)
    assert price == 2199.0
    assert currency == "PKR"
    assert meta["rendered"] is False
    assert meta["fallback_reason"] == "no browser"


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
    assert calls[0]["args"] == scraper.CHROMIUM_ARGS
    assert "--disable-dev-shm-usage" in scraper.CHROMIUM_ARGS


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


# --- Rendered-price integration (real Chromium, local pages only) --------
# Skipped where no browser can launch (CI has the playwright package but
# no browser binary). Locally this runs the REAL render path against
# fake Daraz pages served on 127.0.0.1.

_CLASSIC_PAGE = """<html><head><title>AirBuds_Pro Wireless Earbuds | Daraz.pk</title></head>
<body>
<h1 class="pdp-mod-product-badge-title">AirBuds_Pro Wireless Earbuds</h1>
<div class="pdp-product-price">
  <span class="pdp-price pdp-price_type_normal">Rs. 699</span>
  <span class="pdp-price pdp-price_type_deleted">Rs. 2,199</span>
  <span class="pdp-discount">-68%</span>
</div>
</body></html>"""

# Renamed classes: no "pdp-price" spans at all, plus a per-month
# installment line inside the price box (must NOT be picked).
_RENAMED_PAGE = """<html><head><title>AirBuds_Pro Wireless Earbuds | Daraz.pk</title></head>
<body>
<h1 class="pdp-mod-product-badge-title">AirBuds_Pro Wireless Earbuds</h1>
<div class="pdp-product-price">
  <span class="current-price">Rs. 699</span>
  <span class="was-price"><s>Rs. 2,199</s></span>
  <span class="off">-68%</span>
  <span class="installment">Rs. 116/month</span>
</div>
</body></html>"""


def _serve_page(html: str):
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/product.html"


def _rendered_call(html: str, monkeypatch):
    # Localhost must bypass the egress proxy or Chromium cannot load it.
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    server, url = _serve_page(html)
    try:
        return scraper.fetch_rendered_price(url)
    except scraper.ScrapeError as exc:
        if "Could not launch Chromium" in str(exc) or "Executable doesn't exist" in str(exc):
            pytest.skip(f"no Chromium available: {exc}")
        raise
    finally:
        server.shutdown()
        server.server_close()


def _rendered_price(html: str, monkeypatch) -> float:
    name, price, currency = _rendered_call(html, monkeypatch)
    assert "AirBuds_Pro" in name
    assert currency == "PKR"
    return price


def test_rendered_price_classic_markup(monkeypatch):
    assert _rendered_price(_CLASSIC_PAGE, monkeypatch) == 699.0


def test_rendered_price_renamed_classes_falls_back_to_min(monkeypatch):
    # No pdp-price classes: Strategy 3 (smallest Rs. amount in the price
    # box) must find the 699 sale price, not the 2,199 original nor the
    # 116/month installment.
    assert _rendered_price(_RENAMED_PAGE, monkeypatch) == 699.0


# Price that renders LATE (4s after the title — past the old 2.5s settle).
_LATE_PAGE = """<html><head><title>AirBuds_Pro Wireless Earbuds | Daraz.pk</title></head>
<body>
<h1 class="pdp-mod-product-badge-title">AirBuds_Pro Wireless Earbuds</h1>
<div class="pdp-product-price"></div>
<script>
setTimeout(() => {
  document.querySelector('.pdp-product-price').innerHTML =
    '<span class="pdp-price pdp-price_type_normal">Rs. 699</span>' +
    '<span class="pdp-price pdp-price_type_deleted">Rs. 2,199</span>';
}, 4000);
</script>
</body></html>"""

# Title loads, price box stays empty forever (bot check / blank variant).
_EMPTY_PAGE = """<html><head><title>AirBuds_Pro Wireless Earbuds | Daraz.pk</title></head>
<body>
<h1 class="pdp-mod-product-badge-title">AirBuds_Pro Wireless Earbuds</h1>
<div class="pdp-product-price"></div>
<p>Please verify you are human to continue</p>
</body></html>"""


def test_rendered_price_that_appears_late(monkeypatch):
    # The price renders 4s after the title — past the old 2.5s settle.
    # The new price wait must hold the page open until it appears.
    assert _rendered_price(_LATE_PAGE, monkeypatch) == 699.0


def test_rendered_price_failure_carries_page_snippet(monkeypatch):
    # No price ever appears: the failure must say what the page held,
    # so the dashboard warning can tell a bot check from an empty box.
    with pytest.raises(scraper.ScrapeError) as excinfo:
        _rendered_call(_EMPTY_PAGE, monkeypatch)
    assert "Rendered page had no readable price" in str(excinfo.value)
    assert "verify you are human" in str(excinfo.value).lower()
