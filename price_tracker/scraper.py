"""Price scraping with polite, store-specific scrapers.

Each supported shop is a :class:`BaseScraper` subclass registered by
domain. A new store is added by writing ONE new class — no changes to
the CLI, the dashboard, or the tracker are needed: :func:`check_all`
and the dashboard only ever call :func:`fetch_product_price`, which
dispatches to the registered scraper for the product URL's domain.

Scrapers are pure ``html -> (name, price, currency)`` extractors plus
an optional :meth:`BaseScraper.fetch` override for JS-heavy shops, so
extractors can be unit-tested against saved fixtures without any
network access.

Daraz note: promotion/sale prices are rendered client-side and never appear
in the static HTML, so daraz.pk URLs are rendered in headless Chromium
(Playwright) to always capture the exact selling price — the promotion price
when a discount is active, otherwise the regular price. Static HTML
extraction remains as a fallback when Playwright isn't installed.

Politeness defaults: a real browser User-Agent, a 10s request timeout
(Daraz pages measured ~3-6s in testing — 2s flakes), one retry, and a
1s delay between products during bulk checks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Callable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10  # seconds; Daraz pages measured ~3-6s in probing
MAX_RETRIES = 1  # one retry after the first attempt
RATE_LIMIT_DELAY = 1.0  # seconds between products in bulk checks

Extractor = Callable[[str], tuple[str, float, str]]


class ScrapeError(Exception):
    """Raised when a price cannot be fetched or parsed."""


def parse_price(text: str) -> float:
    """Parse a price string like "Rs. 159,999" or "PKR 1,299.50" to float."""
    # Drop a leading currency marker first so the "." in "Rs." is not
    # mistaken for a decimal point.
    stripped = re.sub(r"^(Rs\.?|PKR)\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\d.]", "", stripped)
    if not cleaned:
        raise ScrapeError(f"Could not parse a price from {text!r}")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ScrapeError(f"Could not parse a price from {text!r}") from exc


def _extract_json_ld(soup: BeautifulSoup) -> dict:
    """Return the first schema.org Product JSON-LD block, or {}."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return {}


def _download(url: str) -> str:
    """GET a page with UA header, timeout and one retry."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_error = exc
            log.warning("Attempt %d failed for %s: %s", attempt + 1, url, exc)
            time.sleep(1)
    raise ScrapeError(f"Failed to download {url}: {last_error}")


#: System Chromium paths tried when Playwright's bundled browser is
#: missing (e.g. Streamlit Cloud, where ``packages.txt`` installs
#: Debian's ``chromium``).
SYSTEM_CHROMIUM_PATHS = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
)

#: Flags for launching Chromium in containers (Streamlit Cloud etc.):
#: no sandbox (non-root container), /dev/shm is tiny (Chromium crashes
#: on launch without the override), and no GPU.
CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


def _browser_proxy() -> dict | None:
    """Playwright proxy config from environment, or None for direct.

    Splits user:pass out of the proxy URL because Chromium handles
    explicit proxy credentials more reliably than embedded ones.
    """
    from urllib.parse import urlparse as _urlparse

    server = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not server:
        return None
    parts = _urlparse(server)
    cfg: dict = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}"}
    if parts.username:
        cfg["username"] = parts.username
    if parts.password:
        cfg["password"] = parts.password
    return cfg


def _launch_browser(p):
    """Launch Chromium for Playwright, falling back to a system install.

    Tries Playwright's bundled browser first, then a system Chromium —
    ``PLAYWRIGHT_CHROMIUM_PATH`` when set, else the usual distro paths.
    The system path matters on hosts that cannot run
    ``playwright install`` (e.g. Streamlit Cloud, where ``packages.txt``
    installs Debian's ``chromium``). ``--no-sandbox`` is required in
    containers. Raises ScrapeError when no browser can be launched.
    """
    candidates: list[dict] = [
        {"headless": True, "proxy": _browser_proxy(), "args": CHROMIUM_ARGS}
    ]
    paths = [os.environ["PLAYWRIGHT_CHROMIUM_PATH"]] if os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") else []
    paths += [c for c in SYSTEM_CHROMIUM_PATHS if os.path.exists(c)]
    for path in paths:
        candidates.append(
            {
                "headless": True,
                "proxy": _browser_proxy(),
                "args": CHROMIUM_ARGS,
                "executable_path": path,
            }
        )
    last_error: Exception | None = None
    for kwargs in candidates:
        try:
            return p.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            last_error = exc
            log.debug(
                "Chromium launch failed (%s): %s",
                kwargs.get("executable_path", "bundled"),
                exc,
            )
    raise ScrapeError(f"Could not launch Chromium: {last_error}")


def fetch_rendered_price(url: str, timeout_ms: int = 30000) -> tuple[str, float, str]:
    """Render a JS-heavy page in headless Chromium and extract the selling price.

    This is the only way to get EXACT prices on Daraz: promotion/sale prices
    are rendered client-side and never appear in the static HTML (which only
    carries the original list price). If a discount is running, this returns
    the discounted price; otherwise the regular price.

    Requires: ``pip install playwright`` and a Chromium binary — either
    ``playwright install chromium`` or a system Chromium (see
    :func:`_launch_browser`; Streamlit Cloud installs one from
    ``packages.txt``). Raises ScrapeError if Playwright is missing, no
    browser can be launched, or the price can't be read.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError(
            "playwright is not installed; run: pip install playwright && "
            "playwright install chromium"
        ) from exc

    js = """() => {
        const out = {name: null, sale: null, snippet: null};
        const nameEl = document.querySelector('h1.pdp-mod-product-badge-title');
        out.name = ((nameEl && nameEl.innerText) || document.title || '').trim();
        // A short slice of the visible page text, for diagnosing a page
        // that renders with no price (bot check? empty price box?).
        out.snippet = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 200);
        // Strategy 1: standard PDP price block (Lazada/Daraz markup)
        const box = document.querySelector('.pdp-product-price');
        let saleEl = box && (
            box.querySelector('.pdp-price_type_normal') ||
            Array.from(box.querySelectorAll('.pdp-price'))
                 .find(e => !/deleted/i.test(e.className))
        );
        // Strategy 2: any pdp-price span on the page, prefer non-deleted
        if (!saleEl) {
            const all = Array.from(document.querySelectorAll('[class*="pdp-price"]'))
                .filter(e => e.innerText && /(Rs\\.?|PKR)/.test(e.innerText));
            saleEl = all.find(e => !/deleted|strike/i.test(e.className)) || all[0] || null;
        }
        // Strategy 3: the sale price is the smallest Rs. amount in the
        // price box (a sale price is always <= the original price). Only
        // used when the class-based rules found nothing — e.g. Daraz
        // renames its price classes. Per-month installment amounts
        // ("Rs. 116/month") are excluded: they are not the selling price.
        let saleText = null;
        if (!saleEl && box) {
            const amounts = [];
            const re = /(?:Rs\\.?|PKR)\\s?([\\d,]+(?:\\.\\d{1,2})?)/g;
            let m;
            while ((m = re.exec(box.innerText)) !== null) {
                if (box.innerText[m.index + m[0].length] === '/') continue;
                amounts.push(parseFloat(m[1].replace(/,/g, '')));
            }
            if (amounts.length) saleText = 'Rs. ' + Math.min(...amounts).toLocaleString('en-US');
        }
        out.sale = (saleEl && saleEl.innerText || '').trim() || saleText;
        return out;
    }"""
    try:
        with sync_playwright() as p:
            browser = _launch_browser(p)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                # Wait on the verified title selector; price markup can vary.
                page.wait_for_selector(
                    "h1.pdp-mod-product-badge-title", timeout=timeout_ms
                )
                # Wait for the PRICE itself, not just the title: Daraz
                # renders the price client-side after the title, and on a
                # slow connection it can take many seconds. If it never
                # appears, carry on anyway — the strategies below still
                # run, and the failure message will say what the page held.
                try:
                    page.wait_for_function(
                        "() => { const box = document.querySelector('.pdp-product-price');"
                        " return box && /(Rs\\\\.?|PKR)/.test(box.innerText); }",
                        timeout=15000,
                    )
                except Exception:
                    pass  # price never appeared; diagnose below from the page text
                page.wait_for_timeout(2500)  # let the client-side price render
                data = page.evaluate(js)
            finally:
                browser.close()
    except Exception as exc:
        raise ScrapeError(f"JS render failed for {url}: {exc}") from exc

    if not isinstance(data, dict):
        raise ScrapeError(f"Could not read rendered price: {data}")
    name = data.get("name") or "Unknown product"
    if not data.get("sale"):
        snippet = (data.get("snippet") or "").strip()
        raise ScrapeError(
            "Rendered page had no readable price"
            + (f" (page text: {snippet!r})" if snippet else " (page text was empty)")
        )
    return name, parse_price(data["sale"]), "PKR"


# ---------------------------------------------------------------------------
# Store scrapers (plugins)
# ---------------------------------------------------------------------------


class BaseScraper(ABC):
    """Interface every store scraper implements.

    To add a store, subclass this, set :attr:`name` and :attr:`domains`,
    implement :meth:`extract`, and decorate the class with
    :func:`register_scraper` — nothing else needs to change. The default
    :meth:`fetch` downloads the static page and extracts from it; shops
    whose prices only render client-side (Daraz) override :meth:`fetch`.
    """

    #: Human-readable store name, e.g. "Daraz".
    name: str = "generic"

    #: Domain substrings this scraper handles, e.g. ("daraz.pk",).
    domains: tuple[str, ...] = ()

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """True when this scraper serves the URL's domain."""
        domain = urlparse(url).netloc.lower()
        return any(d in domain for d in cls.domains)

    @staticmethod
    @abstractmethod
    def extract(html: str) -> tuple[str, float, str]:
        """Extract (name, price, currency) from a product page's HTML."""
        raise NotImplementedError

    def fetch(self, url: str) -> tuple[str, float, str]:
        """Fetch (name, price, currency) for a product URL."""
        return self.extract(_download(url))


SCRAPERS: list[type[BaseScraper]] = []

#: Domain -> extractor, kept for backward compatibility (kept in sync by
#: the registry; ``register_extractor`` also writes here).
EXTRACTORS: dict[str, Extractor] = {}


def register_scraper(cls: type[BaseScraper]) -> type[BaseScraper]:
    """Class decorator registering a store scraper by its domains."""
    SCRAPERS.append(cls)
    for domain in cls.domains:
        EXTRACTORS[domain] = cls.extract
    return cls


def get_scraper(url: str) -> BaseScraper:
    """Return the registered scraper for a product URL's domain.

    Falls back to :class:`GenericScraper` (schema.org JSON-LD) for
    unknown shops.
    """
    for cls in SCRAPERS:
        if cls.can_handle(url):
            return cls()
    return GenericScraper()


def supported_stores() -> list[str]:
    """Names of the registered stores, in registration order."""
    return [cls.name for cls in SCRAPERS]


@register_scraper
class DarazScraper(BaseScraper):
    """Scraper for Daraz.pk — exact selling price via headless Chromium."""

    name = "Daraz"
    domains = ("daraz.pk",)

    @staticmethod
    def extract(html: str) -> tuple[str, float, str]:
        """Extract (name, price, currency) from a Daraz.pk product page.

        Daraz renders prices client-side, but the static HTML embeds a
        ``pdpTrackingData`` JavaScript variable holding escaped JSON with
        ``pdt_name``, ``pdt_price`` (e.g. "Rs. 159,999") and the currency
        code. Falls back to JSON-LD / <title> / visible "Rs." text.

        WARNING: ``pdt_price`` is the LIST price — on a sale page it is
        the struck-through original price, not the selling price (the
        sale price never appears in the static HTML). This extractor is
        only a last-resort fallback; the exact selling price comes from
        the rendered page via :func:`fetch_rendered_price`.
        """
        soup = BeautifulSoup(html, "lxml")
        name: str | None = None
        price: float | None = None
        currency = "PKR"

        # Primary: pdpTrackingData embedded JSON (verified live on daraz.pk).
        match = re.search(r'pdpTrackingData\s*=\s*"((?:[^"\\]|\\.)*)"', html)
        if match:
            try:
                inner = json.loads(json.loads(f'"{match.group(1)}"'))
                name = inner.get("pdt_name") or None
                raw_price = inner.get("pdt_price")
                if raw_price:
                    price = parse_price(raw_price)
                currency = (inner.get("core") or {}).get("currencyCode", "PKR") or "PKR"
            except (json.JSONDecodeError, ScrapeError) as exc:
                log.debug("pdpTrackingData parse failed: %s", exc)

        # Fallbacks for name.
        if not name:
            ld = _extract_json_ld(soup)
            name = ld.get("name") or None
        if not name and soup.title and soup.title.string:
            name = soup.title.string.split("|")[0].strip() or None

        # Fallbacks for price.
        if price is None:
            ld = _extract_json_ld(soup)
            offers = ld.get("offers") or {}
            if isinstance(offers, dict) and offers.get("price"):
                try:
                    price = float(offers["price"])
                    currency = offers.get("priceCurrency", currency)
                except (TypeError, ValueError):
                    pass
        if price is None:
            text = soup.get_text(" ", strip=True)
            m = re.search(r"(?:Rs\.?|PKR)\s?([\d,]+(?:\.\d{1,2})?)", text)
            if m:
                price = parse_price(m.group(1))

        if not name:
            raise ScrapeError("Could not find the product name on the page")
        if price is None:
            raise ScrapeError("Could not find the product price on the page")
        return name, price, currency

    def fetch(self, url: str) -> tuple[str, float, str, dict]:
        """Render in Chromium for the exact selling price; static fallback.

        Returns ``(name, price, currency, meta)`` where ``meta`` says
        whether the price came from the rendered page (``rendered=True``)
        or the static fallback (``rendered=False`` plus a human-readable
        ``fallback_reason``). The static fallback returns the LIST price
        on sale pages — callers must surface that, never record it
        silently (see :func:`tracker.check_all`'s report).
        """
        try:
            name, price, currency = fetch_rendered_price(url)
            return name, price, currency, {"rendered": True}
        except ScrapeError as exc:
            log.warning("JS render failed (%s); falling back to static HTML", exc)
            name, price, currency = self.extract(_download(url))
            return name, price, currency, {
                "rendered": False,
                "fallback_reason": str(exc),
            }


@register_scraper
class PriceOyeScraper(BaseScraper):
    """Scraper for PriceOye.pk — mobile prices via schema.org JSON-LD.

    Verified live on priceoye.pk (Samsung Galaxy A07): the product page
    embeds a JSON-LD Product block with ``name`` and ``offers.price`` /
    ``offers.priceCurrency``. Fallbacks: <title> for the name
    ("<Name> Price in Pakistan ... | Priceoye") and the visible
    "lowest price in Pakistan of Rs. X" text for the price.
    """

    name = "PriceOye"
    domains = ("priceoye.pk",)

    @staticmethod
    def extract(html: str) -> tuple[str, float, str]:
        soup = BeautifulSoup(html, "lxml")
        name: str | None = None
        price: float | None = None
        currency = "PKR"

        ld = _extract_json_ld(soup)
        if ld:
            name = ld.get("name") or None
            offers = ld.get("offers") or {}
            if isinstance(offers, dict) and offers.get("price"):
                try:
                    price = float(offers["price"])
                    currency = offers.get("priceCurrency", currency) or "PKR"
                except (TypeError, ValueError):
                    price = None

        if not name and soup.title and soup.title.string:
            title = soup.title.string.strip()
            name = re.split(r"\s+Price in Pakistan|\|", title)[0].strip() or None

        if price is None:
            text = soup.get_text(" ", strip=True)
            m = re.search(
                r"lowest price in Pakistan of\s*(?:Rs\.?|PKR)\s?([\d,]+(?:\.\d{1,2})?)",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                price = parse_price(m.group(1))

        if not name:
            raise ScrapeError("Could not find the product name on the page")
        if price is None:
            raise ScrapeError("Could not find the product price on the page")
        return name, price, currency


@register_scraper
class TelemartScraper(BaseScraper):
    """Scraper for Telemart.pk / TeleX — sale price from the price block.

    Verified live on telex.pk (Goonj Nexo Wireless Earbuds): the selling
    price sits in ``<span class="product-price__sale">Rs.2,699.00</span>``
    with the original price struck through in ``<s class="compare_price">``.
    Fallback: the embedded ``"price":269900`` minor-units field. The
    visible-text "Rs." fallback is deliberately NOT used here — the page
    also contains a stray "Rs.0", and a wrong price is worse than no price.
    """

    name = "Telemart"
    domains = ("telemart.pk", "telex.pk")

    @staticmethod
    def extract(html: str) -> tuple[str, float, str]:
        soup = BeautifulSoup(html, "lxml")
        name: str | None = None
        price: float | None = None

        if soup.title and soup.title.string:
            # "Goonj Nexo Wireless Earbuds – TeleX (Telemart)"
            title = soup.title.string.strip()
            name = re.split(r"\s+[–—-]\s+", title)[0].strip() or None

        sale = soup.select_one("span.product-price__sale")
        if sale and sale.get_text(strip=True):
            try:
                price = parse_price(sale.get_text(strip=True))
            except ScrapeError:
                price = None

        if price is None:
            # Embedded variant JSON carries the price in minor units.
            m = re.search(r'"price"\s*:\s*(\d+)', html)
            if m:
                minor = int(m.group(1))
                if minor > 0:
                    price = minor / 100.0

        if not name:
            raise ScrapeError("Could not find the product name on the page")
        if price is None:
            raise ScrapeError("Could not find the product price on the page")
        return name, price, "PKR"


class GenericScraper(BaseScraper):
    """Best-effort scraper for unknown shops via schema.org JSON-LD."""

    name = "Generic"
    domains = ()

    @staticmethod
    def extract(html: str) -> tuple[str, float, str]:
        soup = BeautifulSoup(html, "lxml")
        ld = _extract_json_ld(soup)
        name = ld.get("name")
        offers = ld.get("offers") or {}
        price_raw = offers.get("price") if isinstance(offers, dict) else None
        if not name or price_raw is None:
            raise ScrapeError("No schema.org Product/offer data found on the page")
        try:
            price = float(price_raw)
        except (TypeError, ValueError) as exc:
            raise ScrapeError(f"Could not parse offer price {price_raw!r}") from exc
        currency = offers.get("priceCurrency", "PKR") if isinstance(offers, dict) else "PKR"
        return name, price, currency


# ---------------------------------------------------------------------------
# Entry point + backward-compatible aliases
# ---------------------------------------------------------------------------

EXTRACTORS["generic"] = GenericScraper.extract


def register_extractor(domain: str) -> Callable[[Extractor], Extractor]:
    """Legacy decorator registering a function extractor for a domain.

    The function is wrapped in a :class:`BaseScraper` subclass and
    registered, so it also works through :func:`fetch_product_price`.
    Prefer :func:`register_scraper` for new stores.
    """

    def decorator(func: Extractor) -> Extractor:
        cls_name = "".join(part.capitalize() for part in re.split(r"\W+", domain) if part)
        cls = type(
            f"{cls_name}Scraper",
            (BaseScraper,),
            {"name": domain, "domains": (domain,), "extract": staticmethod(func)},
        )
        SCRAPERS.append(cls)
        EXTRACTORS[domain] = func
        return func

    return decorator


#: Backward-compatible aliases for the pre-plugin API.
extract_daraz = DarazScraper.extract
extract_generic = GenericScraper.extract


def fetch_product_price(url: str) -> tuple[str, float, str] | tuple[str, float, str, dict]:
    """Scrape (name, price, currency) for a product URL.

    Dispatches to the registered scraper for the URL's domain: Daraz
    renders in headless Chromium for the exact selling price (promotion
    price when a discount is active, otherwise the regular price, with a
    static-HTML fallback); PriceOye and Telemart parse static HTML;
    unknown shops use the generic JSON-LD extractor.

    JS-rendered shops (Daraz) return a 4th element, a ``meta`` dict:
    ``{"rendered": True}`` when the price came from the rendered page,
    or ``{"rendered": False, "fallback_reason": ...}`` when the static
    fallback ran (the price is then the LIST price on a sale page).
    """
    return get_scraper(url).fetch(url)
