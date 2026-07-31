"""
Validation suite for capital.py -- the Basel III Pillar 3 capital-adequacy
reporting layer.

Same discipline as test_data_jpm.py: capital.py's value is that its
recomputed ratios reproduce JPM's own disclosed percentages. These tests
lock that in.
"""

import pytest

from lcr_optimizer.capital import JPM_CAPITAL_PERIODS, compute_capital_adequacy

PERIOD_KEYS = list(JPM_CAPITAL_PERIODS.keys())


def test_only_documented_quarters_present():
    """
    Guards against someone adding a 2Q25/3Q25 entry without the actual
    Pillar 3 source document (see capital.py's module docstring on why
    those two quarters are deliberately absent).
    """
    assert set(PERIOD_KEYS) == {"jpm_4q25", "jpm_1q26"}


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_recomputed_ratios_match_jpm_disclosed(period_key):
    """
    Recomputing each ratio from raw dollar amounts (capital / RWA or
    capital / leverage exposure) must reproduce JPM's own disclosed
    percentage to within 0.1 percentage points -- JPM rounds its disclosed
    ratios to 1 decimal, so a small gap is expected rounding noise, not an
    error, but anything larger would indicate a transcription mistake in
    the raw dollar figures.
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
    """CET1 + AT1 (implicit) should not exceed Tier 1, and Tier 1 + Tier 2
    must equal Total capital -- a basic internal-consistency check on the
    raw extracted figures, independent of what JPM's own ratios say."""
    d = JPM_CAPITAL_PERIODS[period_key]
    assert d["cet1_capital_mm"] <= d["tier1_capital_mm"]
    assert d["tier1_capital_mm"] + d["tier2_capital_mm"] == pytest.approx(d["total_capital_mm"], abs=1.0)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_rwa_components_sum_to_total(period_key):
    d = JPM_CAPITAL_PERIODS[period_key]
    component_sum = d["rwa_credit_risk_mm"] + d["rwa_market_risk_mm"] + d["rwa_operational_risk_mm"]
    assert component_sum == pytest.approx(d["rwa_total_mm"], abs=1.0)


@pytest.mark.parametrize("period_key", PERIOD_KEYS)
def test_both_quarters_reported_well_capitalized(period_key):
    """
    Both 4Q25 and 1Q26 Pillar 3 reports state the Firm was well-capitalized
    and met all requirements -- the recomputed surplus over every
    requirement should therefore be non-negative for both quarters. This
    is a real, expected finding (not assumed): both quarters happen to
    show comfortable surplus (SLR is the tightest at ~0.8-1.3 points), but
    if a future quarter's real numbers ever showed a shortfall, this test
    would need its assertion revisited against that quarter's actual
    disclosed compliance status, not blindly re-asserted True.
    """
    result = compute_capital_adequacy(period_key)
    assert result.all_requirements_met is True
    assert result.cet1_surplus_pct >= 0
    assert result.tier1_surplus_pct >= 0
    assert result.total_capital_surplus_pct >= 0
    assert result.tier1_leverage_surplus_pct >= 0
    assert result.slr_surplus_pct >= 0
