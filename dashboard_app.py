"""Streamlit dashboard for Price Tracker.

Shows tracked products, Plotly price-trend charts, target prices and
alerts. On first run (empty database) it seeds clearly-labelled demo
data so the dashboard is explorable offline — no network needed until
you press "Check prices now".
"""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from price_tracker import database as db
from price_tracker.alerts import format_alert
from price_tracker.models import Product
from price_tracker.tracker import CheckOutcome, check_all, current_alerts

st.set_page_config(page_title="Price Tracker", page_icon="🏷️", layout="wide")

DEFAULT_DB = os.environ.get("PRICE_TRACKER_DB", "prices.db")

# ---------------------------------------------------------------- demo data

DEMO_PRODUCTS = [
    ("Demo: Wireless Earbuds Pro", "https://www.daraz.pk/products/demo-earbuds.html", 4500.0, 5999.0),
    ("Demo: Mechanical Keyboard", "https://www.daraz.pk/products/demo-keyboard.html", 12000.0, 14500.0),
    ("Demo: Smart Watch Fit", "https://www.daraz.pk/products/demo-watch.html", 8000.0, 9999.0),
]


def seed_demo_data(db_path: str | Path) -> None:
    """Insert 3 demo products with 30 days of deterministic price history."""
    rng = random.Random(42)
    now = datetime.now(timezone.utc)
    for name, url, base, target in DEMO_PRODUCTS:
        product = db.add_product(db_path, url, name=name, target_price=target)
        price = base * 1.15
        for day in range(30, 0, -1):
            price = max(base * 0.9, price * (1 + rng.uniform(-0.04, 0.02)))
            if rng.random() < 0.08:  # occasional sale dip
                price *= rng.uniform(0.85, 0.95)
            checked = (now - timedelta(days=day)).isoformat(timespec="seconds")
            # record_price stamps its own time; write rows directly for history
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO price_points (product_id, price, currency, checked_at)"
                " VALUES (?, ?, ?, ?)",
                (product.id, round(price, 0), "PKR", checked),
            )
            conn.commit()
            conn.close()


# ---------------------------------------------------------------- helpers


def _short(name: str, limit: int = 40) -> str:
    """Shorten a long product name for labels, keeping it readable."""
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _latest_rows(db_path: str | Path) -> pd.DataFrame:
    """One row per product: latest price, previous price, target."""
    rows = []
    for p in db.list_products(db_path):
        history = db.get_history(db_path, p.id) if p.id else []
        latest = history[-1] if history else None
        prev = history[-2].price if len(history) >= 2 else None
        rows.append(
            {
                "id": p.id,
                "Product": _short(p.name, 45),
                "Latest": f"{latest.currency} {latest.price:,.0f}" if latest else "—",
                "Change": (
                    f"{latest.price - prev:+,.0f}" if (latest and prev) else "—"
                ),
                "Target": f"{p.target_price:,.0f}" if p.target_price else "—",
                "Target hit": (
                    "✅" if (latest and p.target_price and latest.price <= p.target_price) else ""
                ),
                "URL": p.url,
            }
        )
    return pd.DataFrame(rows)


def _history_frame(db_path: str | Path, product: Product) -> pd.DataFrame:
    history = db.get_history(db_path, product.id) if product.id else []
    return pd.DataFrame(
        {
            "checked_at": [h.checked_at for h in history],
            "price": [h.price for h in history],
        }
    )


# ---------------------------------------------------------------- app

# The database path is fixed (env PRICE_TRACKER_DB or "prices.db") and
# shown read-only — an editable path field invites accidental data loss.
db_path = DEFAULT_DB
st.sidebar.caption(f"🗄️ Database: {db_path}")
db.init_db(db_path)

if not db.list_products(db_path):
    seed_demo_data(db_path)
    st.info("✨ Seeded with demo data (3 products, 30 days of history) — add your own Daraz.pk links in the sidebar. Demo rows are prefixed with “Demo:”.")

st.title("🏷️ Price Tracker")
st.caption("Track Daraz.pk prices over time — get alerted on price drops.")

if "alerts" not in st.session_state:
    st.session_state["alerts"] = []

with st.sidebar:
    st.header("➕ Track a product")
    with st.form("add_product", clear_on_submit=True):
        url = st.text_input("Daraz product URL")
        name = st.text_input("Name (optional)")
        target = st.number_input("Target price (optional, PKR)", min_value=0.0, value=0.0)
        submitted = st.form_submit_button("Add product")
    if submitted:
        if not url.strip():
            st.warning("Please paste a product URL first.")
        else:
            try:
                p = db.add_product(
                    db_path, url.strip(), name.strip() or None,
                    target if target > 0 else None,
                )
                st.success(f"Tracking #{p.id}: {p.name}")
            except Exception as exc:
                st.error(f"Could not add product: {exc}")

    st.header("🔄 Price check")
    if st.button("Check prices now"):
        with st.spinner("Checking prices..."):
            report: list[CheckOutcome] = []
            st.session_state["alerts"] = check_all(db_path, report=report)
            st.session_state["check_report"] = report
        st.success("Check complete.")
        st.rerun()

    st.header("🗑️ Remove")
    if st.session_state.get("remove_notice"):
        st.success(st.session_state.pop("remove_notice"))
    products = db.list_products(db_path)
    if products:
        label_to_id = {f"#{p.id} {_short(p.name)}": p.id for p in products}
        # No pre-selected product: deleting the wrong row must take intent.
        doomed = st.selectbox(
            "Product to remove", list(label_to_id),
            index=None, placeholder="Select a product…",
        )
        if st.button("Remove product"):
            if doomed is None:
                st.warning("Select a product first.")
            else:
                st.session_state["confirm_remove"] = label_to_id[doomed]
        if st.session_state.get("confirm_remove") is not None:
            pid = st.session_state["confirm_remove"]
            pname = next((p.name for p in products if p.id == pid), "")
            st.warning(
                f"Delete “{_short(pname)}” and all its price history? "
                "This cannot be undone."
            )
            col_yes, col_no = st.columns(2)
            if col_yes.button("Yes, delete"):
                db.remove_product(db_path, pid)
                st.session_state.pop("confirm_remove", None)
                st.session_state["remove_notice"] = f"Removed “{_short(pname)}”."
                st.rerun()
            if col_no.button("Cancel"):
                st.session_state.pop("confirm_remove", None)
                st.rerun()

# ---- alerts panel: stored-history alerts + fresh-check alerts, deduped
seen: set = set()
alerts = []
for a in current_alerts(db_path) + st.session_state["alerts"]:
    key = (a.product_id, a.kind, a.new_price)
    if key not in seen:
        seen.add(key)
        alerts.append(a)
if alerts:
    st.subheader("🔔 Latest alerts")
    for a in alerts:
        st.warning(format_alert(a))

# ---- check report: per-product outcomes from the last "Check prices now"
# A static-fallback warning is shown, not hidden: a recorded price that
# came from static HTML may be the list price, not the sale price.
# Demo rows are sample data with fake URLs — their checks are expected
# to fail, so a red error for them is noise, not meaningful info, and is
# skipped. A real product's failed check still shows its error.
for outcome in st.session_state.get("check_report", []):
    if not outcome.ok:
        if outcome.product_name.startswith("Demo:"):
            continue
        st.error(f"❌ {outcome.product_name}: price check failed ({outcome.error}). Previous price kept.")
    elif outcome.rendered is False:
        st.warning(
            f"⚠️ {outcome.product_name}: exact (sale) price unavailable "
            f"({outcome.fallback_reason}) — recorded price may be the list "
            "price, not the sale price, so target alerts may not fire."
        )

# ---- overview table
st.subheader("📦 Tracked products")
table = _latest_rows(db_path)
st.dataframe(table.drop(columns=["URL"]), width="stretch", hide_index=True)

# ---- trend chart
st.subheader("📈 Price trend")
products = db.list_products(db_path)
if products:
    choice = st.selectbox(
        "Product",
        [f"#{p.id} {_short(p.name, 50)}" for p in products],
    )
    selected = next(p for p in products if f"#{p.id} {_short(p.name, 50)}" == choice)
    frame = _history_frame(db_path, selected)
    if not frame.empty:
        fig = px.line(
            frame, x="checked_at", y="price", markers=True,
            title=f"{_short(selected.name, 60)} — price history (PKR)",
            labels={"checked_at": "Checked at", "price": "Price (PKR)"},
        )
        if selected.target_price:
            fig.add_hline(
                y=selected.target_price, line_dash="dash", line_color="green",
                annotation_text=f"Target {selected.target_price:,.0f}",
            )
        st.plotly_chart(fig, width="stretch")
        if len(frame) < 2:
            st.caption("Only 1 check so far — press “Check prices now” again to build a trend.")
    else:
        st.info("No price history yet — press “Check prices now”.")
