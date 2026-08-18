"""
Validation suite for model_lp.py -- LP relaxation / shadow-price layer
test_lp_relaxation_dual_value_matches_hand_derivation is key: 
extends 2-asset hand-derivable case (test_model.py) to the LCR dual value
"""

import pytest

from lcr_optimizer.data import (
    Asset,
    build_asset_universe,
    build_inflow_profile,
    build_outflow_profile,
    net_cash_outflows,
)
from lcr_optimizer.data_jpm import JPM_PERIODS, build_hybrid_asset_universe
from lcr_optimizer.model import OptimizerConfig, solve
from lcr_optimizer.model_lp import solve_lp_relaxation


def test_lp_relaxation_matches_hand_computed_two_asset():
    """Same setup as test_model.py hand-derivation (C=51, B=40, cost=5.735)
    LP relaxation happens to have integer optimum LP/MIP should coincide exactly, confirming formulations are equivalent"""
    assets = [
        Asset("Cash", "L1", "central_bank", 0.00, "fixed", min_lot=1.0, available=float("inf")),
        Asset("L2A Bond", "L2A", "IssuerX", 0.05, "synthetic", min_lot=1.0, available=1000),
    ]
    config = OptimizerConfig(net_cash_outflows=85.0, benchmark_yield=0.085)
    result = solve_lp_relaxation(assets, config)

    assert result.status == "OPTIMAL"
    by_name = {a.name: a for a in result.allocations}
    assert by_name["Cash"].amount_mm == pytest.approx(51.0, abs=1e-6)
    assert by_name["L2A Bond"].amount_mm == pytest.approx(40.0, abs=1e-6)
    assert result.total_hqla_adjusted == pytest.approx(85.0, abs=1e-6)
    assert result.total_annual_cost_mm == pytest.approx(5.735, abs=1e-6)


def test_lp_relaxation_dual_value_matches_hand_derivation():
    """
    Derivation (same 2-asset setup): at optimum, LCR + 40% L2 cap both bind:
      C + 0.85B = NCO; C = 1.275B
    Differentiate w.r.t. NCO: dB/dNCO=1/2.125, dC/dNCO=1.275/2.125=0.6
    Marginal cost = 0.085*0.6 + 0.035*(1/2.125) = 0.067470588... $mm/yr per $mm NCO
    """
    assets = [
        Asset("Cash", "L1", "central_bank", 0.00, "fixed", min_lot=1.0, available=float("inf")),
        Asset("L2A Bond", "L2A", "IssuerX", 0.05, "synthetic", min_lot=1.0, available=1000),
    ]
    config = OptimizerConfig(net_cash_outflows=85.0, benchmark_yield=0.085)
    result = solve_lp_relaxation(assets, config)

    expected_dual = 0.085 * 0.6 + 0.035 * (1.0 / 2.125)
    assert result.lcr_marginal_cost_mm_per_mm == pytest.approx(expected_dual, abs=1e-9)


def test_lp_objective_is_lower_bound_on_mip():
    """LP cost <= MIP cost always (relaxed integrality)
    Checked on full 16-asset universe 
    toy 2-asset case has zero gap, wouldn't catch violation"""
    outflows = build_outflow_profile()
    inflows = build_inflow_profile()
    nco = net_cash_outflows(outflows, inflows)["net_cash_outflows"]
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=nco, benchmark_yield=0.085)

    mip_result = solve(assets, config)
    lp_result = solve_lp_relaxation(assets, config)

    assert mip_result.status == "OPTIMAL"
    assert lp_result.status == "OPTIMAL"
    assert lp_result.total_annual_cost_mm <= mip_result.total_annual_cost_mm + 1e-6
    assert lp_result.lcr_marginal_cost_mm_per_mm is not None
    assert lp_result.lcr_marginal_cost_mm_per_mm > 0


def test_lp_relaxation_infeasible_matches_mip_infeasible():
    """LP must agree with MIP on feasibility for absurd NCO 
    guards against inconsistent "unbounded" (available=inf) cap convention between two solvers (both must cap at 10,000 lots)"""
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=1_000_000.0, benchmark_yield=0.085)

    mip_result = solve(assets, config)
    lp_result = solve_lp_relaxation(assets, config)

    assert mip_result.status == "INFEASIBLE"
    assert lp_result.status == "INFEASIBLE"


@pytest.mark.parametrize("period_key", list(JPM_PERIODS.keys()))
def test_lp_relaxation_runs_on_real_jpm_data(period_key):
    """Smoke test, real data: must solve OPTIMAL, marginal cost positive and <= benchmark_yield 
    (every real asset has yield_pct=0.0)"""
    period = JPM_PERIODS[period_key]
    assets = build_hybrid_asset_universe(period_key)
    nco = net_cash_outflows(period["build_outflows"](), period["build_inflows"]())["net_cash_outflows"]
    config = OptimizerConfig(net_cash_outflows=nco, benchmark_yield=0.05)

    result = solve_lp_relaxation(assets, config)

    assert result.status == "OPTIMAL"
    assert result.lcr_marginal_cost_mm_per_mm is not None
    assert 0 < result.lcr_marginal_cost_mm_per_mm <= 0.05 + 1e-9
