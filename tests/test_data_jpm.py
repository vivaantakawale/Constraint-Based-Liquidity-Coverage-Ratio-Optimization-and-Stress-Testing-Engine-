"""
Validation suite for real JPM data in data_jpm.py

data_jpm.py's numbers reproduce JPM's disclosed arithmetic
nothing enforces that at import time, so these tests lock in what was verified by hand during extraction

Tolerances: JPM discloses whole-$mm rounded figures, rates.py stores 4 decimals
both introduce rounding noise, so comparisons use rel=1e-3 (0.1%), not exact equality
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
    """net_cash_outflows() over real lines must reproduce JPM's reported NCO
    (excl. maturity-mismatch add-on, not modeled -- limitation 3)"""
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
    """Real HQLA lines (cash + L1 + L2A, haircut-adjusted) must sum to JPM's reported eligible HQLA"""
    period = JPM_PERIODS[period_key]
    disclosed = period["disclosed"]
    hqla = period["build_hqla"]()

    total_adjusted = sum(a.available * (1.0 - (a.haircut or 0.0)) for a in hqla)

    assert total_adjusted == pytest.approx(disclosed["eligible_hqla_mm"], rel=1e-3)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hybrid_universe_preserves_real_tier_totals(period_key):
    """Rescaling in build_hybrid_asset_universe() must be lossless 
    total adjusted HQLA still matches JPM's disclosed eligible HQLA"""
    period = JPM_PERIODS[period_key]
    disclosed = period["disclosed"]
    assets = build_hybrid_asset_universe(period_key)

    total_adjusted = sum(a.available * (1.0 - (a.haircut or 0.0)) for a in assets if a.is_hqla)

    assert total_adjusted == pytest.approx(disclosed["eligible_hqla_mm"], rel=1e-3)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hybrid_universe_drops_level_2b(period_key):
    """Both quarters report $0 Level 2B 
    hybrid universe must contain none"""
    assets = build_hybrid_asset_universe(period_key)
    l2b_tiers = {"L2B_RMBS", "L2B_CORP", "L2B_EQUITY"}
    assert not any(a.tier in l2b_tiers for a in assets)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_hybrid_universe_solves_optimal_against_real_nco(period_key):
    """End to end: real outflows/inflows -> NCO -> hybrid universe -> solver
    Must be OPTIMAL, LCR >= 100%"""
    period = JPM_PERIODS[period_key]
    outflows = period["build_outflows"]()
    inflows = period["build_inflows"]()
    nco = net_cash_outflows(outflows, inflows)["net_cash_outflows"]

    assets = build_hybrid_asset_universe(period_key)
    config = OptimizerConfig(net_cash_outflows=nco, benchmark_yield=0.05)
    result = solve(assets, config)

    assert result.status == "OPTIMAL"
    assert result.lcr_pct >= 100.0 - 1e-6
    assert result.total_hqla_adjusted == pytest.approx(nco, rel=1e-3)  # solver minimizes down to floor


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_other_liquidity_sources_present_and_sane(period_key):
    """Every period's other_liquidity_sources (contingent-liquidity diagnostic input) 
    must have positive components summing to total, magnitude in line with JPM's disclosed order (catches stray zero)"""
    period = JPM_PERIODS[period_key]
    sources = period["other_liquidity_sources"]

    unencumbered = sources["unencumbered_securities_mm"]
    fhlb = sources["fhlb_discount_window_capacity_mm"]

    assert unencumbered == pytest.approx(sources["total_mm"] - fhlb, rel=1e-9)
    assert 400_000 < unencumbered < 700_000  # ~$548-573bn range across the 4 quarters extracted
    assert 400_000 < fhlb < 500_000          # ~$422-450bn range across the 4 quarters extracted


def test_hybrid_universe_tight_concentration_cap_is_infeasible_not_crash():
    """25% concentration cap genuinely infeasible vs. real JPM data (cash + Treasuries dominate)
    Must return INFEASIBLE with diagnostic, not crash"""
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
