"""
Validation suite for scenarios.py stress layer over synthetic bank
Mirrors test_scenarios_jpm.py: guards against rate overrides silently failing to apply 
(typo'd key -> collapses to baseline, still reports OPTIMAL)
"""

import pytest

from lcr_optimizer.data import build_asset_universe
from lcr_optimizer.model import OptimizerConfig
from lcr_optimizer.scenarios import (
    ALL_SCENARIOS,
    BASELINE,
    COMBINED_2008,
    DEPOSIT_RUN,
    WHOLESALE_FREEZE,
    run_scenario,
)


def test_baseline_scenario_reproduces_unstressed_nco():
    """BASELINE has empty overrides, rate must equal line's own baseline exactly, confirming true identity, not near-match"""
    from lcr_optimizer.data import build_inflow_profile, build_outflow_profile, net_cash_outflows

    real_nco = net_cash_outflows(build_outflow_profile(), build_inflow_profile())["net_cash_outflows"]
    assets = build_asset_universe()
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.085)

    run = run_scenario(BASELINE, assets, base_config)

    assert run["net_cash_outflows_mm"] == pytest.approx(real_nco, rel=1e-9)


def test_stress_scenarios_increase_nco_monotonically():
    """Each successively severe scenario must produce strictly larger NCO
    mismatched override key would collapse NCO to baseline, breaking order"""
    assets = build_asset_universe()
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.085)

    baseline = run_scenario(BASELINE, assets, base_config)
    deposit_run = run_scenario(DEPOSIT_RUN, assets, base_config)
    wholesale_freeze = run_scenario(WHOLESALE_FREEZE, assets, base_config)
    combined = run_scenario(COMBINED_2008, assets, base_config)

    nco_baseline = baseline["net_cash_outflows_mm"]
    nco_deposit_run = deposit_run["net_cash_outflows_mm"]
    nco_wholesale_freeze = wholesale_freeze["net_cash_outflows_mm"]
    nco_combined = combined["net_cash_outflows_mm"]

    assert nco_deposit_run > nco_baseline
    assert nco_wholesale_freeze > nco_baseline
    assert nco_combined > nco_deposit_run
    assert nco_combined > nco_wholesale_freeze


def test_all_scenarios_run_without_exception():
    """Smoke test: every scenario returns OPTIMAL/FEASIBLE/INFEASIBLE, never raises"""
    assets = build_asset_universe()
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.085)
    for scenario in ALL_SCENARIOS:
        run = run_scenario(scenario, assets, base_config)
        assert run["result"].status in ("OPTIMAL", "FEASIBLE", "INFEASIBLE")


def test_baseline_solves_optimal():
    """Synthetic universe sized so baseline stays feasible
    infeasible means calibration broken, not meaningful stress finding"""
    assets = build_asset_universe()
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.085)

    run = run_scenario(BASELINE, assets, base_config)

    assert run["result"].status == "OPTIMAL"
    assert run["result"].lcr_pct >= 100.0 - 1e-6
