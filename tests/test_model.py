"""
Validation suite for core optimizer test_hand_computed_two_asset is key one: 
2-asset problem solved by hand, confirms CP SAT model isn't silently wrong before trusting it on full universe
"""

import pytest

from lcr_optimizer.data import (
    Asset,
    build_asset_universe,
    build_inflow_profile,
    build_outflow_profile,
    net_cash_outflows,
)
from lcr_optimizer.model import OptimizerConfig, solve


def test_hand_computed_two_asset():
    """
    Universe: Cash (L1, 0% haircut, 0% yield) + L2A bond (15% haircut, 5% yield)
    Benchmark 8.5%. NCO=$85mm. No cash floor/concentration cap

    Derivation: C=cash, B=bond ($mm). Adjusted: C, 0.85B
      (1) LCR: C + 0.85B >= 85
      (2) L2 cap: 0.85B <= 0.40*(C+0.85B) <=> C >= 1.275B
      Objective: min 0.085C + 0.035B
      Bond cheaper per adj-$ (0.0412 vs 0.085) -> cap (2) binds
      Solve (1),(2) as equalities: C=1.275B, 2.125B=85 -> B=40, C=51
      Check: 51+34=85 (LCR=100%); 60*34=2040==40*51 (cap exact)
      Cost = 0.085*51 + 0.035*40 = 5.735 $mm/yr
    """
    assets = [
        Asset("Cash", "L1", "central_bank", 0.00, "fixed", min_lot=1.0, available=float("inf")),
        Asset("L2A Bond", "L2A", "IssuerX", 0.05, "synthetic", min_lot=1.0, available=1000),
    ]
    config = OptimizerConfig(net_cash_outflows=85.0, benchmark_yield=0.085)
    result = solve(assets, config)

    assert result.status == "OPTIMAL"
    by_name = {a.name: a for a in result.allocations}
    assert by_name["Cash"].amount_mm == pytest.approx(51.0, abs=1e-6)
    assert by_name["L2A Bond"].amount_mm == pytest.approx(40.0, abs=1e-6)
    assert result.total_hqla_adjusted == pytest.approx(85.0, abs=1e-6)
    assert result.lcr_pct == pytest.approx(100.0, abs=1e-4)
    assert result.total_annual_cost_mm == pytest.approx(5.735, abs=1e-3)


def test_l2_and_l2b_caps_respected_on_full_universe():
    """Full 16-asset universe: L2 (2A+2B) <= 40% of total, L2B <= 15%,
    LCR >= 100% (small tolerance for integer-lot rounding)"""
    outflows = build_outflow_profile()
    inflows = build_inflow_profile()
    nco = net_cash_outflows(outflows, inflows)["net_cash_outflows"]
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=nco)
    result = solve(assets, config)

    assert result.status == "OPTIMAL"
    l1 = result.diagnostics["l1_adjusted_mm"]
    l2a = result.diagnostics["l2a_adjusted_mm"]
    l2b = result.diagnostics["l2b_adjusted_mm"]
    total = l1 + l2a + l2b
    assert total == pytest.approx(result.total_hqla_adjusted, abs=1e-6)
    # allow tiny slack for integer lot rounding
    assert (l2a + l2b) <= 0.40 * total + 1e-6
    assert l2b <= 0.15 * total + 1e-6
    assert result.lcr_pct >= 100.0 - 1e-6


def test_infeasible_when_outflows_exceed_available_supply():
    """Absurdly large NCO vs. bounded universe must return INFEASIBLE with diagnostic, 
    not silent wrong answer or crash"""
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=1_000_000.0)  # $1 trillion, way beyond supply
    result = solve(assets, config)

    assert result.status == "INFEASIBLE"
    assert "max_achievable_hqla_mm" in result.diagnostics
    assert result.diagnostics["shortfall_mm"] > 0
    assert result.allocations == []


def test_cash_floor_respected():
    """cash_floor=500.0 must force >= $500mm of 'Cash / central bank reserves'"""
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=5_000.0, cash_floor=500.0)
    result = solve(assets, config)
    assert result.status == "OPTIMAL"
    cash_alloc = next(a for a in result.allocations if a.name == "Cash / central bank reserves")
    assert cash_alloc.amount_mm >= 500.0


def test_issuer_concentration_cap_respected():
    """issuer_concentration_cap=0.20: every issuer's summed adjusted HQLA stays within 20% of total 
    (plus integer-rounding tolerance)"""
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=5_000.0, issuer_concentration_cap=0.20)
    result = solve(assets, config)
    assert result.status == "OPTIMAL"
    total = result.total_hqla_adjusted
    by_issuer = {}
    for a in result.allocations:
        by_issuer[a.issuer] = by_issuer.get(a.issuer, 0.0) + a.adjusted_mm
    for issuer, amt in by_issuer.items():
        assert amt <= 0.20 * total + 1e-3, f"{issuer} exceeds 20% concentration cap"


def test_contingent_liquidity_defaults_to_no_op():
    """contingent_liquidity_mm defaults 0.0 
    infeasible solve must produce contingent_liquidity_* diagnostic keys (feature is opt-in)"""
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=1_000_000.0)
    result = solve(assets, config)

    assert result.status == "INFEASIBLE"
    assert "contingent_liquidity_available_mm" not in result.diagnostics
    assert "contingent_liquidity_sufficient" not in result.diagnostics


def test_contingent_liquidity_sufficient_case():
    """100mm HQLA, 1000mm NCO (900mm shortfall), 950mm contingent liquidity
    950>=900 -> diagnostic reports operationally solvent (status stays INFEASIBLE)"""
    assets = [Asset("Cash", "L1", "central_bank", 0.0, "fixed", min_lot=1.0, available=100.0)]
    config = OptimizerConfig(net_cash_outflows=1_000.0, benchmark_yield=0.05, contingent_liquidity_mm=950.0)
    result = solve(assets, config)

    assert result.status == "INFEASIBLE"  # regulatory LCR result must never flip to OPTIMAL from this
    d = result.diagnostics
    assert d["shortfall_mm"] == pytest.approx(900.0)
    assert d["contingent_liquidity_available_mm"] == pytest.approx(950.0)
    assert d["contingent_liquidity_needed_mm"] == pytest.approx(900.0)
    assert d["contingent_liquidity_sufficient"] is True


def test_contingent_liquidity_insufficient_case():
    """Same shape, contingent liquidity (50mm) too small for 900mm shortfall
    must report sufficient=False"""
    assets = [Asset("Cash", "L1", "central_bank", 0.0, "fixed", min_lot=1.0, available=100.0)]
    config = OptimizerConfig(net_cash_outflows=1_000.0, benchmark_yield=0.05, contingent_liquidity_mm=50.0)
    result = solve(assets, config)

    assert result.status == "INFEASIBLE"
    d = result.diagnostics
    assert d["contingent_liquidity_available_mm"] == pytest.approx(50.0)
    assert d["contingent_liquidity_needed_mm"] == pytest.approx(900.0)
    assert d["contingent_liquidity_sufficient"] is False


def test_non_hqla_assets_never_selected_beyond_cost_minimum():
    """Non-HQLA assets contribute nothing to LCR numerator 
    solver only touches them if cost negative, never to "cheat" LCR requirement"""
    assets = build_asset_universe()
    config = OptimizerConfig(net_cash_outflows=5_000.0)
    result = solve(assets, config)
    non_hqla_names = {a.name for a in assets if not a.is_hqla}
    for alloc in result.allocations:
        if alloc.name in non_hqla_names:
            assert alloc.adjusted_mm == 0.0
