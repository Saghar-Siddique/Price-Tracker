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
