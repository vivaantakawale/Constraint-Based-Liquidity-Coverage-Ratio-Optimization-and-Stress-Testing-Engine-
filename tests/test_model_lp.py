"""
Validation suite for model_lp.py -- the LP relaxation / shadow-price layer.

test_lp_relaxation_matches_hand_computed_dual is the important one: it
extends the exact same hand-derivable 2-asset case used in
test_model.py::test_hand_computed_two_asset (see that docstring for the
full derivation) one step further, to the dual value on the LCR
constraint. That dual is this module's entire reason to exist, so it gets
the same standard of proof as the MIP formulation itself: pencil-and-paper
first, code second.
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
    """
    Same setup as test_model.py::test_hand_computed_two_asset (see that
    docstring for the derivation of C=51, B=40, cost=5.735). The LP
    relaxation of this particular problem happens to have an integer
    optimum (51 and 40 are already integers), so the LP and MIP solutions
    should coincide exactly here -- confirms the LP formulation is
    equivalent to the MIP's, not just "close."
    """
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
    Hand derivation (same 2-asset setup as above): at the optimum, both
    the LCR constraint and the 40% L2 composition cap are simultaneously
    binding:
      C + 0.85B = NCO                    (LCR binding)
      0.60*(0.85B) = 0.40*C  =>  C = 1.275B   (L2 cap binding)
    Differentiating both w.r.t. NCO (marginal analysis):
      dC/dNCO = 1.275 * dB/dNCO
      dC/dNCO + 0.85*dB/dNCO = 1  =>  (1.275+0.85)*dB/dNCO = 1
      dB/dNCO = 1/2.125,  dC/dNCO = 1.275/2.125 = 0.6
    Marginal cost = d(0.085C + 0.035B)/dNCO
                  = 0.085*0.6 + 0.035*(1/2.125)
                  = 0.051 + 0.016470588... = 0.067470588... $mm/yr per $mm NCO
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
    """
    LP relaxation drops integrality, so its optimal cost can only be <=
    the MIP's true integer optimum -- never strictly greater. Checked on
    the full 16-asset synthetic universe (not the toy 2-asset case, which
    happens to have zero integrality gap and wouldn't catch a violation of
    this bound)."""
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
    """
    The LP relaxation must agree with the MIP on feasibility for an
    absurdly large NCO -- this specifically guards against the "unbounded"
    (available=inf) asset cap being defined inconsistently between the two
    solvers (model.py caps it at 10,000 lots; model_lp.py must use the
    exact same convention, or the LP could wrongly report OPTIMAL for an
    NCO the MIP correctly reports INFEASIBLE for).
    """
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=1_000_000.0, benchmark_yield=0.085)

    mip_result = solve(assets, config)
    lp_result = solve_lp_relaxation(assets, config)

    assert mip_result.status == "INFEASIBLE"
    assert lp_result.status == "INFEASIBLE"


@pytest.mark.parametrize("period_key", list(JPM_PERIODS.keys()))
def test_lp_relaxation_runs_on_real_jpm_data(period_key):
    """Smoke test + sanity bounds on real data: must solve OPTIMAL and
    report a positive marginal cost no larger than the benchmark yield
    itself (every real hybrid-universe asset has yield_pct=0.0, so the
    marginal cost can never exceed benchmark_yield - 0)."""
    period = JPM_PERIODS[period_key]
    assets = build_hybrid_asset_universe(period_key)
    nco = net_cash_outflows(period["build_outflows"](), period["build_inflows"]())["net_cash_outflows"]
    config = OptimizerConfig(net_cash_outflows=nco, benchmark_yield=0.05)

    result = solve_lp_relaxation(assets, config)

    assert result.status == "OPTIMAL"
    assert result.lcr_marginal_cost_mm_per_mm is not None
    assert 0 < result.lcr_marginal_cost_mm_per_mm <= 0.05 + 1e-9
