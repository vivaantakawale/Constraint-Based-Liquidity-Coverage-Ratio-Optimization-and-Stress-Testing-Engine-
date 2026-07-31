"""
Validation suite for scenarios_jpm.py -- the stress-scenario layer over
real JPM data.

The critical failure mode this file guards against is *silent* no-op
stress: _StressedLine looks up each scenario's multiplier by stripping the
quarter prefix off a real category key (e.g. "jpm_3q25_stable_retail_deposits"
-> "stable_retail_deposits"). If that stripped suffix ever fails to match a
key in a scenario's multiplier dict -- a typo, a renamed category, a new
quarter whose builder used a different naming convention -- dict.get()'s
default silently falls back to a 1.0 multiplier, i.e. "no stress applied,"
and every scenario would quietly collapse to the baseline while still
reporting OPTIMAL with a plausible-looking number. That's exactly the kind
of hollow-looking-correct bug that's worse than a crash. The monotonicity
test below would fail immediately if that happened, for any quarter.
"""

import pytest

from lcr_optimizer.data_jpm import JPM_PERIODS, build_hybrid_asset_universe
from lcr_optimizer.model import OptimizerConfig
from lcr_optimizer.scenarios_jpm import (
    ALL_JPM_SCENARIOS,
    JPM_BASELINE,
    JPM_COMBINED_2008,
    JPM_DEPOSIT_RUN,
    JPM_WHOLESALE_FREEZE,
    _canonical_suffix,
    run_jpm_scenario,
)

PERIOD_KEYS = list(JPM_PERIODS.keys())


def test_canonical_suffix_strips_quarter_prefix():
    assert _canonical_suffix("jpm_3q25_stable_retail_deposits") == "stable_retail_deposits"
    assert _canonical_suffix("jpm_1q26_derivative_outflow") == "derivative_outflow"


def test_canonical_suffix_rejects_malformed_category():
    with pytest.raises(ValueError):
        _canonical_suffix("not_a_jpm_category")


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_baseline_scenario_reproduces_real_nco(period_key):
    """
    JPM_BASELINE has empty multiplier dicts, so every _StressedLine's rate
    must equal the line's own real rate exactly (multiplier defaults to 1.0
    for every category) -- confirms the baseline case is a true identity,
    not an accidental near-match.
    """
    from lcr_optimizer.data import net_cash_outflows

    period = JPM_PERIODS[period_key]
    real_nco = net_cash_outflows(period["build_outflows"](), period["build_inflows"]())["net_cash_outflows"]

    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)
    run = run_jpm_scenario(JPM_BASELINE, period_key, assets, base_config)

    assert run["net_cash_outflows_mm"] == pytest.approx(real_nco, rel=1e-9)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_stress_scenarios_increase_nco_monotonically(period_key):
    """
    The core guard against silent no-op stress (see module docstring):
    each successively more severe scenario must produce a strictly larger
    NCO than the last, for every quarter. If any scenario's multipliers
    failed to match (typo, renamed category), its NCO would collapse back
    toward the baseline and this ordering would break.
    """
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)

    baseline = run_jpm_scenario(JPM_BASELINE, period_key, assets, base_config)
    deposit_run = run_jpm_scenario(JPM_DEPOSIT_RUN, period_key, assets, base_config)
    wholesale_freeze = run_jpm_scenario(JPM_WHOLESALE_FREEZE, period_key, assets, base_config)
    combined = run_jpm_scenario(JPM_COMBINED_2008, period_key, assets, base_config)

    nco_baseline = baseline["net_cash_outflows_mm"]
    nco_deposit_run = deposit_run["net_cash_outflows_mm"]
    nco_wholesale_freeze = wholesale_freeze["net_cash_outflows_mm"]
    nco_combined = combined["net_cash_outflows_mm"]

    assert nco_deposit_run > nco_baseline
    assert nco_wholesale_freeze > nco_baseline
    assert nco_combined > nco_deposit_run
    assert nco_combined > nco_wholesale_freeze


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_baseline_and_deposit_run_solve_optimal(period_key):
    """
    Baseline and the (comparatively milder) deposit-run scenario must stay
    within what JPM's real disclosed HQLA can cover, for every quarter --
    if these went infeasible it would mean the multiplier calibration is
    unreasonably severe, not a meaningful stress test.
    """
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)

    for scenario in (JPM_BASELINE, JPM_DEPOSIT_RUN):
        run = run_jpm_scenario(scenario, period_key, assets, base_config)
        assert run["result"].status == "OPTIMAL", f"{scenario.name} unexpectedly infeasible for {period_key}"
        assert run["result"].lcr_pct >= 100.0 - 1e-6


def test_severe_scenarios_report_infeasibility_diagnostics_not_crash():
    """
    Wholesale Funding Freeze / Combined 2008 push NCO well past what JPM's
    *actually held* quarterly HQLA stock can cover (real available supply
    is a hard real number here, not a tunable design knob the way the
    synthetic universe's `available` bounds are) -- confirm that surfaces
    as a clean INFEASIBLE with a useful diagnostic, not a crash or a
    silently wrong OPTIMAL.
    """
    period_key = "jpm_4q25"
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)

    for scenario in (JPM_WHOLESALE_FREEZE, JPM_COMBINED_2008):
        run = run_jpm_scenario(scenario, period_key, assets, base_config)
        result = run["result"]
        assert result.status == "INFEASIBLE"
        assert "shortfall_mm" in result.diagnostics
        assert result.diagnostics["shortfall_mm"] > 0


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_contingent_liquidity_covers_severe_scenarios_for_all_quarters(period_key):
    """
    Empirically verified finding (computed once, then locked in as a test
    so it can't silently regress): for every one of the 4 quarters
    extracted, JPM's disclosed non-LCR-eligible contingent liquidity
    (unencumbered securities + FHLB/discount-window capacity, ~$995bn-
    $1.02T depending on quarter) is large enough to cover the shortfall
    under BOTH severe scenarios (~$510-816bn depending on quarter/scenario).

    This is real data producing a real, consistent result across the whole
    series -- not an assumption. If a future quarter's numbers ever failed
    this (e.g. contingent capacity shrinks or a scenario gets recalibrated
    more severe), this test would need its assertion revisited, not just
    silently pass -- that's the point of asserting the specific outcome
    rather than just "the diagnostic key exists."
    """
    period = JPM_PERIODS[period_key]
    contingent = period["other_liquidity_sources"]["total_mm"]
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05, contingent_liquidity_mm=contingent)

    for scenario in (JPM_WHOLESALE_FREEZE, JPM_COMBINED_2008):
        run = run_jpm_scenario(scenario, period_key, assets, base_config)
        d = run["result"].diagnostics
        assert d["contingent_liquidity_sufficient"] is True, (
            f"{period_key}/{scenario.name}: expected contingent liquidity to cover the "
            f"shortfall based on prior empirical check -- if this fails, the underlying "
            f"numbers changed and the finding needs re-verifying, not just re-asserting."
        )


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_all_scenarios_run_without_exception(period_key):
    """Smoke test: every scenario for every quarter must return a result
    object (OPTIMAL or a clean INFEASIBLE), never raise."""
    assets = build_hybrid_asset_universe(period_key)
    base_config = OptimizerConfig(net_cash_outflows=0.0, benchmark_yield=0.05)
    for scenario in ALL_JPM_SCENARIOS:
        run = run_jpm_scenario(scenario, period_key, assets, base_config)
        assert run["result"].status in ("OPTIMAL", "FEASIBLE", "INFEASIBLE")
