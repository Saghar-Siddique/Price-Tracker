# 🏷️ Price Tracker

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![CI](https://github.com/Saghar-Siddique/Price-Tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/Saghar-Siddique/Price-Tracker/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-30%20passing-brightgreen)]()
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**Never overpay again.** Track Daraz.pk product prices over time — add a
product link, and Price Tracker scrapes the current price on demand, stores
every check in SQLite, charts the trend, and alerts you on price drops or
when your target price is hit.

**Perfect for:** bargain hunters, resellers tracking competitor prices, and
anyone who wants to buy at the right moment.

## Features

- 🕷️ **Daraz.pk scraper** — exact selling prices via headless Chromium (Playwright): promotion/discount prices when a sale is active, regular price otherwise
- 🗄️ **Price history in SQLite** — every check timestamped, queryable, yours
- 📊 **Streamlit dashboard** — product table, Plotly trend charts with target-price line, alerts panel
- 🔔 **Smart alerts** — price-drop % vs last check, plus target-price hits
- 🖥️ **Full CLI** — add / list / remove / check / history / report
- 🧪 **Tested** — pytest suite with fixture-based scraper tests (no network in tests); CI runs the suite on every push and PR

## Quickstart

```bash
pip install -r requirements.txt
playwright install chromium   # one-time: downloads the headless browser

# Track a product (optional target price in PKR)
python -m price_tracker.cli add "https://www.daraz.pk/products/<slug>.html" --target 45000

# Check current prices (alerts on drops / target hits)
python -m price_tracker.cli check

# Launch the dashboard
streamlit run dashboard_app.py
```

The dashboard seeds itself with demo data on first run, so you can explore
every chart offline before adding real products.

## CLI usage

```bash
python -m price_tracker.cli add <url> [--name NAME] [--target PRICE]
python -m price_tracker.cli list
python -m price_tracker.cli remove <id>
python -m price_tracker.cli check          # scrape all, store, print alerts
python -m price_tracker.cli history <id> [--limit 50]
python -m price_tracker.cli report         # latest prices, changes, targets
```

The database defaults to `./prices.db` — override with `--db PATH` or the
`PRICE_TRACKER_DB` environment variable (the dashboard respects it too).

Sample products to try: [`data/sample_products.csv`](data/sample_products.csv).

## How the Daraz scraper works

Daraz renders its prices client-side: the static HTML only ever contains the
original list price — promotion/sale prices (like a Rs. 234 flash deal on a
Rs. 1,000 item) are fetched by JavaScript after the page loads. So for
daraz.pk, Price Tracker renders each product page in headless Chromium
(Playwright) and reads the exact price a shopper sees: the promotion price
when a discount is active, otherwise the regular price. If Playwright isn't
installed, it falls back to static HTML extraction (`pdpTrackingData`
embedded JSON, verified live on daraz.pk) with schema.org JSON-LD and visible
price text as further fallbacks. Polite by default: browser User-Agent, 10s
timeout, one retry, 1s delay between products.

## Project structure

```
price_tracker/
  models.py      # Product / PricePoint dataclasses
  database.py    # SQLite schema + CRUD
  scraper.py     # domain-keyed extractors (Daraz first) + polite fetching
  tracker.py     # check_all(): scrape → store → alerts
  alerts.py      # Alert model + formatting
  cli.py         # argparse interface
dashboard_app.py # Streamlit dashboard (Plotly charts, seeded demo data)
tests/
  fixtures/daraz_product.html  # realistic Daraz fixture (no network in tests)
  test_scraper.py / test_database.py / test_tracker.py
data/sample_products.csv       # 3 real Daraz URLs to try
```

## Disclaimer

This tool is for personal, educational use. Scraping may be subject to a
site's Terms of Service — keep check frequency gentle (the built-in 1s
delay helps), respect `robots.txt`, and stop if a site asks you to.

## License

MIT — do whatever you want with it.
