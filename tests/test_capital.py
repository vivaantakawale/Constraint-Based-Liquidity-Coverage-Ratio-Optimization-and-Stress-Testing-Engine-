"""
Validation suite for capital.py Basel III Pillar 3 reporting layer 
same discipline as test_data_jpm.py: recomputed ratios must reproduce JPM's disclosed percentages
"""

import pytest

from lcr_optimizer.capital import JPM_CAPITAL_PERIODS, compute_capital_adequacy

PERIOD_KEYS = list(JPM_CAPITAL_PERIODS.keys())


def test_only_documented_quarters_present():
    """Guards against 2Q25/3Q25 entry without actual Pillar 3 source doc"""
    assert set(PERIOD_KEYS) == {"jpm_4q25", "jpm_1q26"}


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_recomputed_ratios_match_jpm_disclosed(period_key):
    """Recomputed ratio must match JPM's disclosed percentage within 0.1pp (JPM rounds to 1 decimal) 
    larger gap means transcription error
    """
    disclosed = JPM_CAPITAL_PERIODS[period_key]["disclosed_ratios_pct"]
    result = compute_capital_adequacy(period_key)

    assert result.cet1_ratio_pct == pytest.approx(disclosed["cet1"], abs=0.1)
    assert result.tier1_ratio_pct == pytest.approx(disclosed["tier1"], abs=0.1)
    assert result.total_capital_ratio_pct == pytest.approx(disclosed["total_capital"], abs=0.1)
    assert result.tier1_leverage_ratio_pct == pytest.approx(disclosed["tier1_leverage"], abs=0.1)
    assert result.slr_pct == pytest.approx(disclosed["slr"], abs=0.1)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_capital_components_sum_correctly(period_key):
    """CET1 <= Tier1, Tier1+Tier2 == Total capital internal consistency
    check on raw figures, independent of JPM's own ratios
    """
    d = JPM_CAPITAL_PERIODS[period_key]
    assert d["cet1_capital_mm"] <= d["tier1_capital_mm"]
    assert d["tier1_capital_mm"] + d["tier2_capital_mm"] == pytest.approx(d["total_capital_mm"], abs=1.0)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_rwa_components_sum_to_total(period_key):
    """Credit+market+operational RWA must sum to disclosed rwa_total_mm"""
    d = JPM_CAPITAL_PERIODS[period_key]
    component_sum = d["rwa_credit_risk_mm"] + d["rwa_market_risk_mm"] + d["rwa_operational_risk_mm"]
    assert component_sum == pytest.approx(d["rwa_total_mm"], abs=1.0)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_both_quarters_reported_well_capitalized(period_key):
    """Both quarters' Pillar 3 reports state well capitalized
    all requirements met every surplus must be non-negative 
    (real finding, SLR tightest at ~0.8-1.3pp; revisit if future quarter shows real shortfall)"""
    result = compute_capital_adequacy(period_key)
    assert result.all_requirements_met is True
    assert result.cet1_surplus_pct >= 0
    assert result.tier1_surplus_pct >= 0
    assert result.total_capital_surplus_pct >= 0
    assert result.tier1_leverage_surplus_pct >= 0
    assert result.slr_surplus_pct >= 0
