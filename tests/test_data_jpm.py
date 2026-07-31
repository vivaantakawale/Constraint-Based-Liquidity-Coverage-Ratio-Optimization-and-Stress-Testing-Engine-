"""
Validation suite for the real JPMorgan Chase data in data_jpm.py.

The point of this file is narrow but important: data_jpm.py's whole value
is that its numbers reproduce JPM's own disclosed arithmetic. Nothing in
Python enforces that at import time -- a typo'd balance or a rate copied
into the wrong dict would silently produce a plausible-looking but wrong
NCO/HQLA, and nothing would catch it without these tests. Each check here
was originally verified by hand in the extraction session; this file locks
that verification in so a future edit can't quietly regress it.

Tolerances: JPM discloses whole-$mm figures already rounded from its own
underlying (unpublished) precision, and rates.py stores each implied rate
to 4 decimal places -- both introduce a few $mm of rounding noise on
figures in the hundreds of billions, so comparisons use a relative
tolerance (pytest.approx's default rel=1e-6 is too tight; these use
rel=1e-3, i.e. up to 0.1%) rather than exact equality.
"""

import pytest

from lcr_optimizer.data import net_cash_outflows
from lcr_optimizer.data_jpm import (
    JPM_PERIODS,
    build_hybrid_asset_universe,
)
from lcr_optimizer.model import OptimizerConfig, solve

PERIOD_KEYS = list(JPM_PERIODS.keys())


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_net_cash_outflows_matches_jpm_disclosed(period_key):
    """
    net_cash_outflows() run over this quarter's real outflow/inflow lines
    must reproduce JPM's own reported NCO (excluding the maturity-mismatch
    add-on, which this project doesn't model -- see data_jpm.py's module
    docstring, limitation 3).
    """
    period = JPM_PERIODS[period_key]
    outflows = period["build_outflows"]()
    inflows = period["build_inflows"]()
    disclosed = period["disclosed"]

    result = net_cash_outflows(outflows, inflows)

    assert result["total_outflow"] == pytest.approx(disclosed["total_cash_outflow_mm"], rel=1e-3)
    assert result["total_inflow_uncapped"] == pytest.approx(disclosed["total_cash_inflow_mm"], rel=1e-3)
    assert result["net_cash_outflows"] == pytest.approx(
        disclosed["net_cash_outflow_excl_maturity_mismatch_mm"], rel=1e-3
    )


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hqla_universe_matches_jpm_disclosed(period_key):
    """
    The real (non-hybrid) HQLA line items -- cash + Level 1 securities +
    Level 2A securities, each haircut-adjusted -- must sum to JPM's own
    reported eligible HQLA for the quarter.
    """
    period = JPM_PERIODS[period_key]
    disclosed = period["disclosed"]
    hqla = period["build_hqla"]()

    total_adjusted = sum(a.available * (1.0 - (a.haircut or 0.0)) for a in hqla)

    assert total_adjusted == pytest.approx(disclosed["eligible_hqla_mm"], rel=1e-3)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hybrid_universe_preserves_real_tier_totals(period_key):
    """
    build_hybrid_asset_universe() rescales the synthetic instrument split
    to match real tier totals -- confirm that rescaling is lossless (the
    hybrid universe's total adjusted HQLA still matches JPM's disclosed
    eligible HQLA, same as the non-hybrid build_hqla_universe_jpm_* does).
    """
    period = JPM_PERIODS[period_key]
    disclosed = period["disclosed"]
    assets = build_hybrid_asset_universe(period_key)

    total_adjusted = sum(a.available * (1.0 - (a.haircut or 0.0)) for a in assets if a.is_hqla)

    assert total_adjusted == pytest.approx(disclosed["eligible_hqla_mm"], rel=1e-3)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hybrid_universe_drops_level_2b(period_key):
    """Both real quarters report $0 average eligible Level 2B HQLA -- the
    hybrid universe must not contain any Level 2B instrument for either."""
    assets = build_hybrid_asset_universe(period_key)
    l2b_tiers = {"L2B_RMBS", "L2B_CORP", "L2B_EQUITY"}
    assert not any(a.tier in l2b_tiers for a in assets)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hybrid_universe_solves_optimal_against_real_nco(period_key):
    """
    End-to-end: real outflows/inflows -> real NCO -> hybrid asset universe
    -> solver. Must be OPTIMAL (real available HQLA comfortably exceeds
    real NCO for both quarters) and the resulting LCR must be >= 100%.
    """
    period = JPM_PERIODS[period_key]
    outflows = period["build_outflows"]()
    inflows = period["build_inflows"]()
    nco = net_cash_outflows(outflows, inflows)["net_cash_outflows"]

    assets = build_hybrid_asset_universe(period_key)
    config = OptimizerConfig(net_cash_outflows=nco, benchmark_yield=0.05)
    result = solve(assets, config)

    assert result.status == "OPTIMAL"
    assert result.lcr_pct >= 100.0 - 1e-6
    assert result.total_hqla_adjusted == pytest.approx(nco, rel=1e-3)  # solver minimizes down to the floor


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_other_liquidity_sources_present_and_sane(period_key):
    """
    Every period must carry an "other_liquidity_sources" entry (used by
    the contingent-liquidity infeasibility diagnostic in model.py), with
    positive component values whose sum equals the reported total, and a
    total roughly in line with JPM's own disclosed order of magnitude
    (a few hundred billion each for unencumbered securities and FHLB/
    discount-window capacity, per quarter -- catches a stray extra/missing
    zero, not just a missing key).
    """
    period = JPM_PERIODS[period_key]
    sources = period["other_liquidity_sources"]

    unencumbered = sources["unencumbered_securities_mm"]
    fhlb = sources["fhlb_discount_window_capacity_mm"]

    assert unencumbered == pytest.approx(sources["total_mm"] - fhlb, rel=1e-9)
    assert 400_000 < unencumbered < 700_000  # ~$548-573bn range across the 4 quarters extracted
    assert 400_000 < fhlb < 500_000          # ~$422-450bn range across the 4 quarters extracted


def test_hybrid_universe_tight_concentration_cap_is_infeasible_not_crash():
    """
    A 25% single-issuer concentration cap is genuinely infeasible against
    real JPM data (cash + Treasuries alone dominate real HQLA -- see
    dashboard/app.py's caption on this). Confirm the solver reports
    INFEASIBLE with a useful diagnostic rather than crashing or silently
    returning a wrong answer.
    """
    period_key = "jpm_4q25"
    period = JPM_PERIODS[period_key]
    outflows = period["build_outflows"]()
    inflows = period["build_inflows"]()
    nco = net_cash_outflows(outflows, inflows)["net_cash_outflows"]

    assets = build_hybrid_asset_universe(period_key)
    config = OptimizerConfig(net_cash_outflows=nco, issuer_concentration_cap=0.25, benchmark_yield=0.05)
    result = solve(assets, config)

    assert result.status == "INFEASIBLE"
    assert "shortfall_mm" in result.diagnostics
    assert result.diagnostics["shortfall_mm"] > 0
