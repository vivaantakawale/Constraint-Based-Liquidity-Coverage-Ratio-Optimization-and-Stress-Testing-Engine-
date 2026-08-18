"""
Validation suite for dashboard/app.py via Streamlit's headless AppTest harness
runs script in process, exposes rendered elements for assertions

Targets dashboard wiring, not LCR math (covered elsewhere)
catches wrong dict key, silently-stopped tab render, forgotten escape_dollars() call

One big sequential test as each AppTest.from_file() costs ~1-1.5s regardless of scope
reusing one instance + .set_value().run() costs only ~0.1-0.3s per state change
Trade off: failure reports against this one test name, not specific behavior

Treasury yield fetch is mocked no real network calls/CI flakiness
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lcr_optimizer import market_data
from lcr_optimizer.data_jpm import JPM_PERIODS

DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "app.py"
FIXED_YIELDS = {"UST_1M": 0.042, "UST_2Y": 0.038, "_source": "test-fixed"}


def _assert_validation_tab_reconciles(data_tab, quarter: str):
    """Checks rendered Validation tab against JPM's disclosed figures
    ARGS: data_tab: AppTest tab handle (at.tabs[2])
        quarter: str, JPM_PERIODS key
    RETURNS: None, asserts internally
    """
    disclosed = JPM_PERIODS[quarter]["disclosed"]
    compare_df = data_tab.dataframe[0].value.set_index("Metric")

    hqla_row = compare_df.loc["Total available HQLA ($mm)"]
    assert hqla_row["This project"] == pytest.approx(disclosed["eligible_hqla_mm"], rel=1e-3)
    assert hqla_row["JPM disclosed"] == disclosed["eligible_hqla_mm"]

    nco_row = compare_df.loc["Net cash outflows, excl. maturity mismatch ($mm)"]
    assert nco_row["This project"] == pytest.approx(disclosed["net_cash_outflow_excl_maturity_mismatch_mm"], rel=1e-3)


def test_dashboard_end_to_end(monkeypatch):
    """Walks app.py through every sidebar state worth checking: 
    default load, quarter with/without capital data, scenario switch, known-infeasible concentration cap
    No explicit args/return pytest drives via `monkeypatch`
    """
    monkeypatch.setattr(market_data, "fetch_treasury_yields", lambda: dict(FIXED_YIELDS))
    at = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=30)
    at.run()

    # Baseline: default quarter (first in JPM_PERIODS) Baseline scenario
    assert not at.exception
    assert len(at.warning) == 0
    assert len(at.tabs) == 3
    labels = [m.label for m in at.metric]
    assert labels == [
        "Net Cash Outflows",
        "Total HQLA (adjusted)",
        "LCR",
        "Annual Opportunity Cost",
        "Marginal Cost of NCO",
    ]
    lcr_metric = next(m for m in at.metric if m.label == "LCR")
    assert lcr_metric.value == "100.0%"  # solver minimizes cost down to exactly the LCR floor
    nco_before = next(m for m in at.metric if m.label == "Net Cash Outflows").value
    default_quarter = list(JPM_PERIODS.keys())[0]
    _assert_validation_tab_reconciles(at.tabs[2], default_quarter)  # dashboard glue not tested elsewhere

    # Switch to quarter with real capital data (jpm_4q25)
    # NCO changes, capital tab shows metrics + a "well-capitalized" success message
    at.sidebar.radio[0].set_value("jpm_4q25").run()
    assert not at.exception
    nco_after = next(m for m in at.metric if m.label == "Net Cash Outflows").value
    assert nco_after != nco_before
    data_tab = at.tabs[2]
    assert len(data_tab.metric) == 5
    assert any("Well-capitalized" in s.value for s in data_tab.success)
    _assert_validation_tab_reconciles(data_tab, "jpm_4q25")

    # Switch to quarter with no Pillar 3 data (jpm_2q25) 
    # capital tab must show the explainer caption not empty/broken
    at.sidebar.radio[0].set_value("jpm_2q25").run()
    assert not at.exception
    data_tab = at.tabs[2]
    assert len(data_tab.metric) == 0
    assert any("4Q25 and 1Q26" in c.value for c in data_tab.caption)

    # Switching scenario selectbox re-solves without error 
    # proves scenario selection wiring path works at all (which quarter/scenario combination is picked doesn't matter forwiring check
    # correctness of each scenario's numbers is test_scenarios_jpm.py's job)
    at.sidebar.selectbox[0].set_value("Idiosyncratic Shock").run()
    assert not at.exception
    assert len(at.tabs) == 3

    # 25% single-issuer concentration cap is known infeasible against real JPM data 
    # (test_data_jpm.py::test_hybrid_universe_tight_concentration_cap_is_infeasible_not_crash)
    # st.stop() must prevent metrics/tabs from rendering, and every dynamic $ amount in warning/info text must be escaped 
    # (exact bug escape_dollars() exists to prevent Streamlit renders bare $...$ pair as broken LaTeX)
    at.sidebar.checkbox[0].set_value(True).run()
    at.sidebar.slider[1].set_value(25).run()
    assert not at.exception
    assert len(at.metric) == 0
    assert len(at.tabs) == 0
    assert len(at.warning) == 1
    assert "fails the stress test" in at.warning[0].value
    assert r"\$" in at.warning[0].value
    assert len(at.info) == 1  # contingent-liquidity assessment
    assert r"\$" in at.info[0].value
