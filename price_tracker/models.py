"""Core data models for Price Tracker."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Product:
    """A product being tracked.

    Attributes:
        name: Human-readable product name.
        url: Product page URL (used to scrape the current price).
        target_price: Optional alert threshold — an alert fires when the
            live price drops to (or below) this value.
        id: Database row id (None until saved).
        created_at: ISO-8601 timestamp of when tracking started.
    """

    name: str
    url: str
    target_price: float | None = None
    id: int | None = None
    created_at: str = field(default_factory=_utcnow)


@dataclass
class PricePoint:
    """One recorded price observation for a product.

    Attributes:
        product_id: FK to :class:`Product`.
        price: Observed price as a float (currency units, e.g. PKR).
        currency: ISO currency code, e.g. "PKR".
        id: Database row id (None until saved).
        checked_at: ISO-8601 timestamp of the observation.
    """

    product_id: int
    price: float
    currency: str = "PKR"
    id: int | None = None
    checked_at: str = field(default_factory=_utcnow)
