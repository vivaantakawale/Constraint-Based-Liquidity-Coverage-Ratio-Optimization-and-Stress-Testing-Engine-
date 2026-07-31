"""
Real JPMorgan Chase & Co. regulatory capital data (Basel III Pillar 3),
extracted from public filings. Companion to data_jpm.py's LCR-side real
data -- same "real numbers where disclosed, honestly scoped where not"
approach, applied to the capital-adequacy side of the balance sheet.

WHY THIS EXISTS: data_jpm.py's module docstring flagged the 4Q25 Pillar 3
Report as "kept here only as a cross-reference for a future capital-
adequacy module" -- this is that module.

WHY IT'S A REPORTING LAYER, NOT AN OPTIMIZER CONSTRAINT: LCR and capital
adequacy are two independent Basel III pillars with different denominators
(net cash outflows vs. risk-weighted assets / leverage exposure). There is
no meaningful optimization decision connecting them in this project's
scope -- the HQLA portfolio being sized here (on the order of $0.8-1.6T of
net cash outflows, see data_jpm.py) is a small fraction of JPM's ~$2.0-2.1T
total RWA and ~$5.3-5.6T total leverage exposure. To see whether it could
ever bind: JPM's actual capital conservation buffer / SLR buffer above its
regulatory floor (see compute_capital_adequacy() below) each represent on
the order of $50-150bn of headroom at 4Q25/1Q26 -- far larger than any
plausible swing in the LCR-sized HQLA portfolio modeled elsewhere in this
project could move. Bolting an SLR/RWA constraint onto model.py's
optimizer would therefore be a constraint that can never bind given this
project's actual numbers -- exactly the kind of decorative-but-inert
feature this project has otherwise avoided. This module instead does what
data_jpm.py does for LCR: extract JPM's real disclosed capital figures and
cross-validate that recomputing the ratios from raw dollar amounts
reproduces JPM's own reported percentages.

DATA AVAILABILITY: only 4Q25 and 1Q26 are covered here, because those are
the only two quarters for which the full Pillar 3 Regulatory Capital
Disclosures Report was provided (2Q25/3Q25's Pillar 3 reports were never
supplied -- only their LCR Disclosure reports were, which is why
data_jpm.py's LCR-side data covers all 4 quarters but this module covers
only 2). Do not add 2Q25/3Q25 entries here without the actual source
document -- extrapolating capital figures from unrelated quarters would
violate this project's real-data-only rule for JPM-specific numbers.

Sources: JPMorganChase, "Pillar 3 Regulatory Capital Disclosures," for the
quarterly periods ended December 31, 2025 ("4Q25 Pillar 3 Report") and
March 31, 2026 ("1Q26 Pillar 3 Report"). All $ in millions unless noted.
Figures are JPMorgan Chase & Co. (the Firm), not JPMorgan Chase Bank, N.A.
"""

from dataclasses import dataclass

JPM_4Q25_CAPITAL = {
    "as_of": "December 31, 2025",
    "cet1_capital_mm": 288_469,
    "tier1_capital_mm": 307_630,
    "tier2_capital_mm": 21_332,
    "total_capital_mm": 328_962,
    "rwa_credit_risk_mm": 1_493_805,
    "rwa_market_risk_mm": 92_998,
    "rwa_operational_risk_mm": 458_446,
    "rwa_total_mm": 2_045_249,
    "adjusted_average_assets_mm": 4_472_394,   # Tier 1 leverage ratio denominator
    "total_leverage_exposure_mm": 5_302_001,    # SLR denominator
    "disclosed_ratios_pct": {
        "cet1": 14.1,
        "tier1": 15.0,
        "total_capital": 16.1,
        "tier1_leverage": 6.9,
        "slr": 5.8,
    },
    "requirements_bhc_pct": {
        "cet1_min": 11.5,
        "tier1_min": 13.0,
        "total_capital_min": 15.0,
        "tier1_leverage_min": 4.0,
        "slr_min": 5.0,   # 3.0% base + 2.0% eSLR buffer for BHC [4Q25 P3 p.7 footnote (f)]
    },
    "tlac": {
        "external_tlac_bn": 563.7,
        "external_tlac_pct_of_rwa": 27.6,
        "external_tlac_requirement_pct": 23.0,
        "eligible_ltd_bn": 246.0,
        "eligible_ltd_pct_of_rwa": 12.0,
        "eligible_ltd_requirement_pct": 10.5,
    },
}

JPM_1Q26_CAPITAL = {
    "as_of": "March 31, 2026",
    "cet1_capital_mm": 291_152,
    "tier1_capital_mm": 310_317,
    "tier2_capital_mm": 24_038,
    "total_capital_mm": 334_355,
    "rwa_credit_risk_mm": 1_490_577,
    "rwa_market_risk_mm": 112_869,
    "rwa_operational_risk_mm": 457_895,
    "rwa_total_mm": 2_061_341,
    "adjusted_average_assets_mm": 4_702_980,
    "total_leverage_exposure_mm": 5_576_930,
    "disclosed_ratios_pct": {
        "cet1": 14.1,
        "tier1": 15.1,
        "total_capital": 16.2,
        "tier1_leverage": 6.6,
        "slr": 5.6,
    },
    "requirements_bhc_pct": {
        "cet1_min": 11.5,
        "tier1_min": 13.0,
        "total_capital_min": 15.0,
        "tier1_leverage_min": 4.0,
        "slr_min": 4.3,   # 3.0% base + 1.25% eSLR buffer for BHC, early-adopted 1/1/2026 [1Q26 P3 p.6]
    },
    "tlac": {
        "external_tlac_bn": 572.0,
        "external_tlac_pct_of_rwa": 27.8,
        "external_tlac_requirement_pct": 23.0,
        "eligible_ltd_bn": 249.8,
        "eligible_ltd_pct_of_rwa": 12.1,
        "eligible_ltd_requirement_pct": 10.5,
    },
}

JPM_CAPITAL_PERIODS = {
    "jpm_4q25": JPM_4Q25_CAPITAL,
    "jpm_1q26": JPM_1Q26_CAPITAL,
}


@dataclass
class CapitalAdequacyResult:
    period_key: str
    as_of: str
    cet1_ratio_pct: float
    tier1_ratio_pct: float
    total_capital_ratio_pct: float
    tier1_leverage_ratio_pct: float
    slr_pct: float
    cet1_surplus_pct: float          # ratio - requirement, in percentage points
    tier1_surplus_pct: float
    total_capital_surplus_pct: float
    tier1_leverage_surplus_pct: float
    slr_surplus_pct: float
    all_requirements_met: bool


def compute_capital_adequacy(period_key: str) -> CapitalAdequacyResult:
    """
    Recomputes each capital ratio from raw dollar amounts (not simply
    echoing JPM's disclosed percentages) -- this is the same "reproduce,
    don't just repeat" validation discipline data_jpm.py applies to
    NCO/HQLA. See tests/test_capital.py for the check that this matches
    JPM's own disclosed ratios.
    """
    d = JPM_CAPITAL_PERIODS[period_key]
    req = d["requirements_bhc_pct"]

    cet1_pct = 100.0 * d["cet1_capital_mm"] / d["rwa_total_mm"]
    tier1_pct = 100.0 * d["tier1_capital_mm"] / d["rwa_total_mm"]
    total_pct = 100.0 * d["total_capital_mm"] / d["rwa_total_mm"]
    tier1_leverage_pct = 100.0 * d["tier1_capital_mm"] / d["adjusted_average_assets_mm"]
    slr_pct = 100.0 * d["tier1_capital_mm"] / d["total_leverage_exposure_mm"]

    cet1_surplus = cet1_pct - req["cet1_min"]
    tier1_surplus = tier1_pct - req["tier1_min"]
    total_surplus = total_pct - req["total_capital_min"]
    tier1_leverage_surplus = tier1_leverage_pct - req["tier1_leverage_min"]
    slr_surplus = slr_pct - req["slr_min"]

    return CapitalAdequacyResult(
        period_key=period_key,
        as_of=d["as_of"],
        cet1_ratio_pct=cet1_pct,
        tier1_ratio_pct=tier1_pct,
        total_capital_ratio_pct=total_pct,
        tier1_leverage_ratio_pct=tier1_leverage_pct,
        slr_pct=slr_pct,
        cet1_surplus_pct=cet1_surplus,
        tier1_surplus_pct=tier1_surplus,
        total_capital_surplus_pct=total_surplus,
        tier1_leverage_surplus_pct=tier1_leverage_surplus,
        slr_surplus_pct=slr_surplus,
        all_requirements_met=min(cet1_surplus, tier1_surplus, total_surplus, tier1_leverage_surplus, slr_surplus) >= 0,
    )
