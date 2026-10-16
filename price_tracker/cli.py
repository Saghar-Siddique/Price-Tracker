"""Command-line interface for Price Tracker.

Usage:
    price-tracker add <url> [--name NAME] [--target PRICE]
    price-tracker list
    price-tracker remove <id>
    price-tracker check
    price-tracker history <id> [--limit N]
    price-tracker report

The database defaults to ./prices.db (override with --db or PRICE_TRACKER_DB).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from . import database as db
from .alerts import format_alert
from .tracker import check_all

DEFAULT_DB = Path(os.environ.get("PRICE_TRACKER_DB", "prices.db"))


def _db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )


def cmd_add(args: argparse.Namespace) -> int:
    try:
        product = db.add_product(args.db, args.url, args.name, args.target)
    except sqlite3.IntegrityError:
        print(f"Already tracking: {args.url}", file=sys.stderr)
        return 1
    print(f"Tracking #{product.id}: {product.name}")
    if product.target_price is not None:
        print(f"  target price: {product.target_price:,.0f}")
    print("  (the real product name is filled in on the first `check`)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    products = db.list_products(args.db)
    if not products:
        print("No products tracked yet. Add one with: price-tracker add <url>")
        return 0
    for p in products:
        latest = db.get_latest(args.db, p.id) if p.id else None
        price = f"{latest.currency} {latest.price:,.0f}" if latest else "—"
        target = f"  [target {p.target_price:,.0f}]" if p.target_price else ""
        print(f"#{p.id}  {p.name}\n     latest: {price}{target}\n     {p.url}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    if db.remove_product(args.db, args.product_id):
        print(f"Removed product #{args.product_id} and its price history.")
        return 0
    print(f"No product with id #{args.product_id}.", file=sys.stderr)
    return 1


def cmd_check(args: argparse.Namespace) -> int:
    products = db.list_products(args.db)
    if not products:
        print("No products tracked yet. Add one with: price-tracker add <url>")
        return 0
    print(f"Checking {len(products)} product(s)...")
    alerts = check_all(args.db)
    if alerts:
        print("\nAlerts:")
        for alert in alerts:
            print(" ", format_alert(alert))
    else:
        print("No alerts — prices are steady.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    product = db.get_product(args.db, args.product_id)
    if product is None:
        print(f"No product with id #{args.product_id}.", file=sys.stderr)
        return 1
    history = db.get_history(args.db, args.product_id, limit=args.limit)
    if not history:
        print(f"No price history for #{product.id} yet. Run: price-tracker check")
        return 0
    print(f"Price history for #{product.id} — {product.name}:")
    for point in history:
        print(f"  {point.checked_at}  {point.currency} {point.price:,.0f}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    products = db.list_products(args.db)
    if not products:
        print("No products tracked yet.")
        return 0
    print(f"{'ID':<4}{'Product':<42}{'Latest':<16}{'Change':<14}Target")
    print("-" * 95)
    for p in products:
        history = db.get_history(args.db, p.id) if p.id else []
        if not history:
            print(f"{p.id:<4}{p.name[:40]:<42}{'—':<16}{'—':<14}—")
            continue
        latest = history[-1]
        if len(history) >= 2:
            prev = history[-2].price
            diff = latest.price - prev
            change = f"{diff:+,.0f} ({diff / prev * 100:+.1f}%)" if prev else "—"
        else:
            change = "first check"
        target = f"{p.target_price:,.0f}" if p.target_price else "—"
        hit = "  ✅ HIT" if p.target_price and latest.price <= p.target_price else ""
        print(
            f"{p.id:<4}{p.name[:40]:<42}"
            f"{latest.currency} {latest.price:,.0f}{'':<6}{change:<14}{target}{hit}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="price-tracker",
        description="Track Daraz.pk product prices and get alerts on drops.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Track a new product URL.")
    p_add.add_argument("url", help="Product page URL (e.g. a Daraz.pk link).")
    p_add.add_argument("--name", help="Display name (default: the URL).")
    p_add.add_argument("--target", type=float, help="Alert when price <= this.")
    _db_arg(p_add)
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List tracked products.")
    _db_arg(p_list)
    p_list.set_defaults(func=cmd_list)

    p_rm = sub.add_parser("remove", help="Stop tracking a product.")
    p_rm.add_argument("product_id", type=int)
    _db_arg(p_rm)
    p_rm.set_defaults(func=cmd_remove)

    p_check = sub.add_parser("check", help="Scrape current prices for all products.")
    _db_arg(p_check)
    p_check.set_defaults(func=cmd_check)

    p_hist = sub.add_parser("history", help="Show price history for a product.")
    p_hist.add_argument("product_id", type=int)
    p_hist.add_argument("--limit", type=int, default=50)
    _db_arg(p_hist)
    p_hist.set_defaults(func=cmd_history)

    p_rep = sub.add_parser("report", help="Latest prices, changes and targets.")
    _db_arg(p_rep)
    p_rep.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
