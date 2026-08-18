"""
Validation suite for scenarios_jpm.py stress layer over real JPM data

Critical failure mode guarded: silent no-op stress
If _StressedLine's stripped category suffix fails to match multiplier dict key (typo, renamed category), 
it silently defaults to 1.0 (no stress) and every scenario collapses to baseline while still reporting OPTIMAL
Monotonicity test below catches that immediately

All three non-baseline tiers calibrated to stay OPTIMAL (severe but survivable)
no dedicated "always INFEASIBLE" test here
Genuine infeasibility covered elsewhere (test_data_jpm.py concentration-cap test, test_model.py hand-crafted cases)
"""

import pytest

from lcr_optimizer.data_jpm import JPM_PERIODS, build_hybrid_asset_universe
from lcr_optimizer.model import OptimizerConfig
from lcr_optimizer.scenarios_jpm import (
    ALL_JPM_SCENARIOS,
    JPM_BASELINE,
    JPM_IDIOSYNCRATIC,
    JPM_MARKET_WIDE,
    JPM_SEVERELY_ADVERSE,
    _canonical_suffix,
    run_jpm_scenario,
)

PERIOD_KEYS = list(JPM_PERIODS.keys())


def test_canonical_suffix_strips_quarter_prefix():
    """Strip must be identical regardless of quarter
    lets one multiplier dict apply across all four quarters"""
    assert _canonical_suffix("jpm_3q25_stable_retail_deposits") == "stable_retail_deposits"
    assert _canonical_suffix("jpm_1q26_derivative_outflow") == "derivative_outflow"


def test_canonical_suffix_rejects_malformed_category():
    """Malformed key must raise ValueError, not return wrong suffix silently"""
    with pytest.raises(ValueError):
        _canonical_suffix("not_a_jpm_category")


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_baseline_scenario_reproduces_real_nco(period_key):
    """JPM_BASELINE empty multipliers
    rate must equal real rate exactly (default 1.0), confirming true identity"""
    from lcr_optimizer.data import net_cash_outflows

    period = JPM_PERIODS[period_key]
    real_nco = net_cash_outflows(period["build_outflows"](), period["build_inflows"]())["net_cash_outflows"]

    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)
    run = run_jpm_scenario(JPM_BASELINE, period_key, assets, base_config)

    assert run["net_cash_outflows_mm"] == pytest.approx(real_nco, rel=1e-9)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_stress_scenarios_increase_nco_monotonically(period_key):
    """Core guard against silent no-op stress: each severer scenario must produce strictly larger NCO, every quarter 
    mismatched multiplier key would collapse NCO to baseline, breaking order"""
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)

    baseline = run_jpm_scenario(JPM_BASELINE, period_key, assets, base_config)
    idiosyncratic = run_jpm_scenario(JPM_IDIOSYNCRATIC, period_key, assets, base_config)
    market_wide = run_jpm_scenario(JPM_MARKET_WIDE, period_key, assets, base_config)
    severely_adverse = run_jpm_scenario(JPM_SEVERELY_ADVERSE, period_key, assets, base_config)

    nco_baseline = baseline["net_cash_outflows_mm"]
    nco_idiosyncratic = idiosyncratic["net_cash_outflows_mm"]
    nco_market_wide = market_wide["net_cash_outflows_mm"]
    nco_severely_adverse = severely_adverse["net_cash_outflows_mm"]

    assert nco_idiosyncratic > nco_baseline
    assert nco_market_wide > nco_baseline
    assert nco_severely_adverse > nco_idiosyncratic
    assert nco_severely_adverse > nco_market_wide


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_all_scenarios_solve_optimal(period_key):
    """All four scenarios, incl Severely Adverse, must stay within real disclosed HQLA, every quarter deliberate calibration (severe but survivable)
    INFEASIBLE here means calibration drifted, not data change"""
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)

    for scenario in ALL_JPM_SCENARIOS:
        run = run_jpm_scenario(scenario, period_key, assets, base_config)
        assert run["result"].status == "OPTIMAL", f"{scenario.name} unexpectedly infeasible for {period_key}"
        assert run["result"].lcr_pct >= 100.0 - 1e-6


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_all_scenarios_run_without_exception(period_key):
    """Smoke test: every scenario/quarter returns OPTIMAL/FEASIBLE/INFEASIBLE, never raises"""
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)
    for scenario in ALL_JPM_SCENARIOS:
        run = run_jpm_scenario(scenario, period_key, assets, base_config)
        assert run["result"].status in ("OPTIMAL", "FEASIBLE", "INFEASIBLE")
