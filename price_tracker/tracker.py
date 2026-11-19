"""Scheduled/periodic price checking orchestration.

``check_all`` scrapes every tracked product, stores the new price point,
and returns the alerts raised by this round (price drops vs the previous
check, and target-price hits). A 1s polite delay separates products.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from . import database as db
from .alerts import Alert
from .scraper import RATE_LIMIT_DELAY, ScrapeError, fetch_product_price

log = logging.getLogger(__name__)

FetchFn = Callable[[str], tuple[str, float, str]]


def check_all(
    db_path: str | Path,
    fetch: FetchFn | None = None,
) -> list[Alert]:
    """Check every tracked product once; return alerts raised.

    Args:
        db_path: Path to the SQLite database.
        fetch: Callable ``url -> (name, price, currency)``. Defaults to
            the live scraper; tests inject a fake.

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
            name, price, currency = fetch(product.url)
        except (ScrapeError, Exception) as exc:  # never abort a round
            log.warning("Check failed for %s (%s): %s", product.name, product.url, exc)
            continue

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
