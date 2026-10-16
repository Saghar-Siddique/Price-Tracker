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
from price_tracker.tracker import check_all

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
                "Product": p.name,
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

db_path = st.sidebar.text_input("Database path", value=DEFAULT_DB)
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
    with st.form("add_product"):
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
        with st.spinner("Rendering pages in headless browser for exact prices..."):
            st.session_state["alerts"] = check_all(db_path)
        st.success("Check complete.")
        st.rerun()

    st.header("🗑️ Remove")
    products = db.list_products(db_path)
    if products:
        label_to_id = {f"#{p.id} {p.name[:40]}": p.id for p in products}
        doomed = st.selectbox("Product to remove", list(label_to_id))
        if st.button("Remove product"):
            db.remove_product(db_path, label_to_id[doomed])
            st.rerun()

# ---- alerts panel
alerts = st.session_state["alerts"]
if alerts:
    st.subheader("🔔 Latest alerts")
    for a in alerts:
        st.warning(format_alert(a))

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
        [f"#{p.id} {p.name}" for p in products],
    )
    selected = next(p for p in products if f"#{p.id} {p.name}" == choice)
    frame = _history_frame(db_path, selected)
    if not frame.empty:
        fig = px.line(
            frame, x="checked_at", y="price", markers=True,
            title=f"{selected.name} — price history (PKR)",
            labels={"checked_at": "Checked at", "price": "Price (PKR)"},
        )
        if selected.target_price:
            fig.add_hline(
                y=selected.target_price, line_dash="dash", line_color="green",
                annotation_text=f"Target {selected.target_price:,.0f}",
            )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No price history yet — press “Check prices now”.")
