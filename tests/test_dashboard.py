"""Dashboard render test via Streamlit AppTest.

Uses a temp database; the app seeds clearly-labelled demo data on first
run, so no network is touched. The "Check prices now" button is never
clicked here (it would hit the live web).
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "dashboard_app.py")


def _run(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("PRICE_TRACKER_DB", str(tmp_path / "dash.db"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_dashboard_renders_with_seeded_demo_data(tmp_path, monkeypatch):
    at = _run(tmp_path, monkeypatch)
    assert any("Price Tracker" in str(t.value) for t in at.title)
    # 3 demo products seeded -> overview table has 3 rows
    assert len(at.dataframe) >= 1
    assert len(at.dataframe[0].value) == 3


def test_dashboard_has_chart_and_controls(tmp_path, monkeypatch):
    at = _run(tmp_path, monkeypatch)
    # product selectbox + exactly one Plotly trend chart rendered
    assert len(at.selectbox) >= 1
    assert len(at.get("plotly_chart")) == 1
    # sidebar controls exist
    assert any("Check prices now" in str(b.label) for b in at.button)


def test_add_product_form_via_ui(tmp_path, monkeypatch):
    at = _run(tmp_path, monkeypatch)
    # Fill the sidebar add-product form (add() never touches the network)
    url_input = next(
        w for w in at.sidebar.text_input if w.label == "Daraz product URL"
    )
    url_input.set_value("https://www.daraz.pk/products/demo-widget-i1-s1.html")
    submit = next(b for b in at.sidebar.button if b.label == "Add product")
    submit.click().run()
    assert not at.exception, at.exception
    # 3 seeded demo products + 1 added = 4 rows in the overview table
    assert len(at.dataframe[0].value) == 4


def test_dashboard_shows_alert_banner_on_load(tmp_path, monkeypatch):
    """Issue #1: seeded demo data is all at/below target, so the alerts
    panel must render banners on a fresh load — no button press needed."""
    at = _run(tmp_path, monkeypatch)
    assert len(at.warning) >= 1
    assert any("target reached" in str(w.value) for w in at.warning)


def test_dashboard_shows_static_fallback_warning(tmp_path, monkeypatch):
    from price_tracker.tracker import CheckOutcome

    at = _run(tmp_path, monkeypatch)
    at.session_state["check_report"] = [
        CheckOutcome(1, "Test Earbuds", ok=True, rendered=False, fallback_reason="no browser here")
    ]
    at.run()
    assert not at.exception, at.exception
    assert any("exact (sale) price unavailable" in str(w.value) for w in at.warning)


def test_dashboard_shows_failed_check_error(tmp_path, monkeypatch):
    from price_tracker.tracker import CheckOutcome

    at = _run(tmp_path, monkeypatch)
    at.session_state["check_report"] = [
        CheckOutcome(1, "Test Earbuds", ok=False, error="boom")
    ]
    at.run()
    assert not at.exception, at.exception
    assert any("price check failed" in str(e.value) for e in at.error)


def test_dashboard_hides_failed_check_error_for_demo_products(tmp_path, monkeypatch):
    """Demo rows are sample data with fake URLs — their checks are
    expected to fail, so their errors must not show. A real product's
    failed check must still show its error."""
    from price_tracker.tracker import CheckOutcome

    at = _run(tmp_path, monkeypatch)
    at.session_state["check_report"] = [
        CheckOutcome(1, "Demo: Wireless Earbuds Pro", ok=False, error="boom"),
        CheckOutcome(2, "Test Earbuds", ok=False, error="boom"),
    ]
    at.run()
    assert not at.exception, at.exception
    assert any("Test Earbuds" in str(e.value) for e in at.error)
    assert not any("Demo: Wireless Earbuds Pro" in str(e.value) for e in at.error)
