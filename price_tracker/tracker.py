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

        if name and name != product.name:
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
