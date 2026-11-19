"""Scheduled/periodic price checking orchestration.

``check_all`` scrapes every tracked product, stores the new price point,
and returns the alerts raised by this round (price drops vs the previous
check, and target-price hits). A 1s polite delay separates products.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import database as db
from .alerts import Alert
from .scraper import RATE_LIMIT_DELAY, ScrapeError, fetch_product_price

log = logging.getLogger(__name__)

#: A fetch result: ``(name, price, currency)``, optionally with a 4th
#: ``meta`` dict (JS-rendered shops say whether the price came from the
#: rendered page or a static fallback).
FetchFn = Callable[[str], tuple]


@dataclass
class CheckOutcome:
    """One product's result from a :func:`check_all` round.

    ``rendered`` is True when the price came from the rendered page,
    False when a static-HTML fallback ran (the price may then be the
    LIST price on a sale page), and None for shops where the distinction
    does not apply. ``error`` is set when the check failed outright.
    """

    product_id: int
    product_name: str
    ok: bool
    rendered: bool | None = None
    fallback_reason: str | None = None
    error: str | None = None


def check_all(
    db_path: str | Path,
    fetch: FetchFn | None = None,
    report: list[CheckOutcome] | None = None,
) -> list[Alert]:
    """Check every tracked product once; return alerts raised.

    Args:
        db_path: Path to the SQLite database.
        fetch: Callable ``url -> (name, price, currency)``. Defaults to
            the live scraper; tests inject a fake.
        report: Optional list that receives one :class:`CheckOutcome`
            per product, so callers can show what happened per product —
            especially when a check fell back to static HTML (list price)
            or failed. Pass ``None`` to skip it.

    Scrape failures are logged and skipped — one bad product never
    aborts the whole round.
    """
    fetch = fetch or fetch_product_price
    alerts: list[Alert] = []
    products = db.list_products(db_path)

    for i, product in enumerate(products):
        if i > 0:
            time.sleep(RATE_LIMIT_DELAY)
        assert product.id is not None  # always set for rows read from the DB
        try:
            result = fetch(product.url)
        except (ScrapeError, Exception) as exc:  # never abort a round
            log.warning("Check failed for %s (%s): %s", product.name, product.url, exc)
            if report is not None:
                report.append(
                    CheckOutcome(product.id, product.name, ok=False, error=str(exc))
                )
            continue

        name, price, currency = result[0], result[1], result[2]
        meta = result[3] if len(result) > 3 and isinstance(result[3], dict) else {}
        if report is not None:
            report.append(
                CheckOutcome(
                    product.id,
                    product.name,
                    ok=True,
                    rendered=meta.get("rendered"),
                    fallback_reason=meta.get("fallback_reason"),
                )
            )

        if name and (not product.name or product.name == product.url):
            # Fill in a missing name only. A name the user typed at add
            # time must stick — overwriting it with the scraped page title
            # (e.g. a 100-char Daraz title) is a bug, not a sync.
            db.update_product_name(db_path, product.id, name)
            product.name = name

        previous = db.get_latest(db_path, product.id)
        point = db.record_price(db_path, product.id, price, currency)

        if previous is not None and price < previous.price:
            alerts.append(
                Alert(
                    product_id=product.id,
                    product_name=product.name,
                    kind="price_drop",
                    old_price=previous.price,
                    new_price=price,
                    currency=currency,
                    checked_at=point.checked_at,
                )
            )
        if product.target_price is not None and price <= product.target_price:
            alerts.append(
                Alert(
                    product_id=product.id,
                    product_name=product.name,
                    kind="target_hit",
                    old_price=previous.price if previous else None,
                    new_price=price,
                    currency=currency,
                    checked_at=point.checked_at,
                )
            )
    return alerts


def current_alerts(db_path: str | Path) -> list[Alert]:
    """Derive alerts from stored history — no scraping.

    What the dashboard shows on load: a ``target_hit`` for every product
    whose latest stored price is at/below its target, and a
    ``price_drop`` where the latest price fell vs the previous check.
    Same Alert shapes as :func:`check_all`, so ``format_alert`` renders
    them identically. Products with no price history raise nothing.
    """
    alerts: list[Alert] = []
    for product in db.list_products(db_path):
        assert product.id is not None  # always set for rows read from the DB
        history = db.get_history(db_path, product.id)
        if not history:
            continue
        latest = history[-1]
        previous = history[-2] if len(history) >= 2 else None
        if previous is not None and latest.price < previous.price:
            alerts.append(
                Alert(
                    product_id=product.id,
                    product_name=product.name,
                    kind="price_drop",
                    old_price=previous.price,
                    new_price=latest.price,
                    currency=latest.currency,
                    checked_at=latest.checked_at,
                )
            )
        if product.target_price is not None and latest.price <= product.target_price:
            alerts.append(
                Alert(
                    product_id=product.id,
                    product_name=product.name,
                    kind="target_hit",
                    old_price=previous.price if previous else None,
                    new_price=latest.price,
                    currency=latest.currency,
                    checked_at=latest.checked_at,
                )
            )
    return alerts
