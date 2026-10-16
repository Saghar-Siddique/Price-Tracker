"""Price-drop and target-price alerts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Alert:
    """An event worth notifying the user about.

    Attributes:
        product_id: Tracked product the alert belongs to.
        product_name: Snapshot of the product name.
        kind: "price_drop" (price fell vs the previous check) or
            "target_hit" (price is at/below the user's target price).
        old_price: Previous observed price (None for first check).
        new_price: Current observed price.
        currency: ISO currency code.
        checked_at: ISO-8601 timestamp of the observation.
    """

    product_id: int
    product_name: str
    kind: str
    old_price: float | None
    new_price: float
    currency: str = "PKR"
    checked_at: str = field(default_factory=_utcnow)

    @property
    def drop_pct(self) -> float | None:
        """Percentage drop vs the previous price, or None."""
        if self.old_price and self.old_price > 0 and self.new_price < self.old_price:
            return round((self.old_price - self.new_price) / self.old_price * 100, 1)
        return None


def format_alert(alert: Alert) -> str:
    """One-line human-readable summary of an alert."""
    if alert.kind == "price_drop" and alert.drop_pct is not None:
        return (
            f"🔻 {alert.product_name}: {alert.currency} {alert.old_price:,.0f} → "
            f"{alert.currency} {alert.new_price:,.0f} "
            f"(-{alert.drop_pct}%)"
        )
    if alert.kind == "target_hit":
        return (
            f"🎯 {alert.product_name}: target reached at "
            f"{alert.currency} {alert.new_price:,.0f}"
        )
    return (
        f"ℹ️ {alert.product_name}: {alert.currency} {alert.new_price:,.0f} "
        f"({alert.kind})"
    )
