"""SQLite persistence layer for Price Tracker.

Schema
------
products(id, name, url UNIQUE, target_price, created_at)
price_points(id, product_id FK, price, currency, checked_at)

All timestamps are stored as ISO-8601 strings in UTC.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import PricePoint, Product

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    target_price REAL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price       REAL NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'PKR',
    checked_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_points_product
    ON price_points(product_id, checked_at);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with Row factory and FK enforcement."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    """Create the schema if it does not exist yet."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def add_product(
    db_path: str | Path,
    url: str,
    name: str | None = None,
    target_price: float | None = None,
) -> Product:
    """Insert a product and return it with its new id.

    Raises:
        sqlite3.IntegrityError: if the URL is already tracked.
    """
    init_db(db_path)
    product = Product(name=name or url, url=url, target_price=target_price)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO products (name, url, target_price, created_at)"
            " VALUES (?, ?, ?, ?)",
            (product.name, product.url, product.target_price, product.created_at),
        )
        product.id = cur.lastrowid
    return product


def list_products(db_path: str | Path) -> list[Product]:
    """Return all tracked products, oldest first."""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    return [
        Product(
            id=r["id"],
            name=r["name"],
            url=r["url"],
            target_price=r["target_price"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def get_product(db_path: str | Path, product_id: int) -> Product | None:
    """Return a single product by id, or None."""
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    if row is None:
        return None
    return Product(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        target_price=row["target_price"],
        created_at=row["created_at"],
    )


def remove_product(db_path: str | Path, product_id: int) -> bool:
    """Delete a product and its price history. Returns True if removed."""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM price_points WHERE product_id = ?", (product_id,))
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return cur.rowcount > 0


def record_price(
    db_path: str | Path,
    product_id: int,
    price: float,
    currency: str = "PKR",
) -> PricePoint:
    """Append a price observation and return it with its new id."""
    init_db(db_path)
    point = PricePoint(product_id=product_id, price=price, currency=currency)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO price_points (product_id, price, currency, checked_at)"
            " VALUES (?, ?, ?, ?)",
            (point.product_id, point.price, point.currency, point.checked_at),
        )
        point.id = cur.lastrowid
    return point


def get_history(
    db_path: str | Path, product_id: int, limit: int = 500
) -> list[PricePoint]:
    """Return price history for a product, oldest first (up to *limit*)."""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM price_points WHERE product_id = ?"
            " ORDER BY checked_at ASC, id ASC LIMIT ?",
            (product_id, limit),
        ).fetchall()
    return [
        PricePoint(
            id=r["id"],
            product_id=r["product_id"],
            price=r["price"],
            currency=r["currency"],
            checked_at=r["checked_at"],
        )
        for r in rows
    ]


def get_latest(db_path: str | Path, product_id: int) -> PricePoint | None:
    """Return the most recent price observation, or None."""
    history = get_history(db_path, product_id, limit=1_000_000)
    return history[-1] if history else None


def update_product_name(db_path: str | Path, product_id: int, name: str) -> None:
    """Rename a product (used after the scraper learns its real title)."""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("UPDATE products SET name = ? WHERE id = ?", (name, product_id))
