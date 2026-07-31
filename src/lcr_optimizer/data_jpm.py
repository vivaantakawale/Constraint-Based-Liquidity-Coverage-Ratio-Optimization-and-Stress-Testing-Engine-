"""
Real JPMorgan Chase & Co. balance-sheet / LCR data, extracted from public
filings. Companion to data.py's synthetic bank -- same dataclasses
(OutflowLine, InflowLine, Asset), real dollar figures.

Sources (all publicly filed / disclosed, $ in millions unless noted):
  [LCR]     JPMorganChase, "Liquidity Coverage Ratio Disclosure," quarterly
            period ended December 31, 2025 ("4Q25 LCR Report"). Required
            filing under the US LCR public disclosure rule (12 CFR 249
            Subpart R). Figures are the firm's *average* balances/weighted
            amounts for the three months ended 12/31/2025, not a point-in-
            time snapshot.
  [P3]      JPMorganChase, "Pillar 3 Regulatory Capital Disclosures," for
            the quarterly period ended December 31, 2025 ("4Q25 Pillar 3
            Report"). Capital-side only (CET1, RWA, SLR) -- not used for
            LCR figures below, kept here only as a cross-reference for a
            future capital-adequacy module.

IMPORTANT LIMITATIONS (read before treating this as a drop-in replacement
for data.py):

1. Aggregate only, not instrument-level. JPM's LCR disclosure reports
   HQLA/outflows/inflows in ~11-20 *category* buckets (e.g. "operational
   deposit outflow": $827,655mm), not per-instrument or per-counterparty --
   that granularity is confidential/supervisory for every bank. The 15-25
   line-item Asset universe in data.py (specific corp bonds, specific
   sovereigns) has no public JPM equivalent; build_hqla_universe() below is
   necessarily just 3 lines (cash, L1 securities, L2A securities).
2. No Level 2B at all. JPM reported $0 average eligible Level 2B HQLA for
   4Q25 [LCR p.2] -- not an omission here, that's the real number.
3. Maturity mismatch add-on ($38,929mm) is NOT modeled. JPM's reported
   Total Net Cash Outflow ($868,500mm) = the sum-of-categories NCO computed
   by net_cash_outflows() below ($829,571mm, verified to match exactly)
   PLUS a $38,929mm "maturity mismatch add-on" that has no BCBS238/data.py
   equivalent -- it's a US-rule-specific adjustment for outflow/inflow
   timing mismatches within the 30-day window. Feeding this module's
   outflow/inflow lines through model.py's net_cash_outflows() will
   therefore UNDERSTATE JPM's actual reported NCO by that amount, and the
   resulting LCR will read ~116% rather than JPM's reported 111%.
4. Rates are blended/implied, not structural. Each rate in
   rates.JPM_4Q25_OUTFLOW_RATES is a realized weighted-average across many
   underlying counterparties and products (e.g. "non-operational wholesale
   funding" blends everything from hedge fund deposits at 100% down to
   fully-insured non-operational corporate deposits at 5%) -- it is not a
   single BCBS238 paragraph rate the way data.py's synthetic categories are.

Everything below is real, dated data as of 4Q25 (quarter ended 12/31/2025).
"""

from dataclasses import replace

from . import rates
from .data import Asset, InflowLine, OutflowLine, build_asset_universe


def build_outflow_profile_jpm_4q25() -> list[OutflowLine]:
    """
    Real JPM outflow categories [LCR p.2, "CASH OUTFLOW AMOUNTS" table].
    `balance` is the average *unweighted* amount; OutflowLine.rate looks up
    the implied rate added to rates.OUTFLOW_RATES, so .stressed_outflow
    reproduces JPM's disclosed average weighted amount for each line.
    """
    return [
        OutflowLine("Stable retail deposit outflow", "jpm_4q25_stable_retail_deposits", 670_316),
        OutflowLine("Other retail funding outflow", "jpm_4q25_other_retail_funding", 405_325),
        OutflowLine("Brokered deposit outflow", "jpm_4q25_brokered_deposits", 83_024),
        OutflowLine("Operational deposit outflow", "jpm_4q25_operational_deposits", 827_655),
        OutflowLine("Non-operational wholesale funding outflow", "jpm_4q25_nonoperational_wholesale_funding", 537_727),
        OutflowLine("Unsecured debt outflow", "jpm_4q25_unsecured_debt", 8_538),
        OutflowLine("Secured wholesale funding & asset exchange outflow", "jpm_4q25_secured_wholesale_funding", 1_144_167),
        OutflowLine("Derivative exposure & collateral outflow", "jpm_4q25_derivative_outflow", 105_655),
        OutflowLine("Credit & liquidity facility drawdown outflow", "jpm_4q25_credit_liquidity_facilities", 619_294),
        OutflowLine("Other contractual funding obligation outflow", "jpm_4q25_other_contractual_funding", 5_582),
        OutflowLine("Other contingent funding obligation outflow", "jpm_4q25_other_contingent_funding", 441_055),
    ]


def build_inflow_profile_jpm_4q25() -> list[InflowLine]:
    """Real JPM inflow categories [LCR p.2, "CASH INFLOW AMOUNTS" table]."""
    return [
        InflowLine("Secured lending & asset exchange inflow", "jpm_4q25_secured_lending_inflow", 1_046_727),
        InflowLine("Retail cash inflow", "jpm_4q25_retail_inflow", 34_645),
        InflowLine("Unsecured wholesale cash inflow", "jpm_4q25_unsecured_wholesale_inflow", 37_381),
        InflowLine("Net derivative cash inflow", "jpm_4q25_derivative_inflow", 13_727),
        InflowLine("Securities cash inflow", "jpm_4q25_securities_inflow", 5_178),
        InflowLine("Broker-dealer segregated account inflow", "jpm_4q25_broker_dealer_segregated_inflow", 32_860),
        InflowLine("Other cash inflow", "jpm_4q25_other_inflow", 473),
    ]


def build_hqla_universe_jpm_4q25() -> list[Asset]:
    """
    Real JPM average eligible HQLA [LCR p.3]. Only 3 lines exist publicly
    (no instrument-level breakdown) -- see module docstring, limitation 1.
    `available` is set to the disclosed average balance itself: unlike
    data.py's synthetic universe (where `available` bounds a market the
    solver chooses how much of to buy), here the whole balance is already
    what JPM actually held, so there is no "buy up to X" decision to model
    -- available == balance is the closest honest representation of a
    single realized data point, not a market-depth assumption.
    yield_pct is left at 0.0 / "unknown": JPM's LCR disclosure reports fair
    values for LCR-eligibility purposes, not yields; no public source gives
    a blended yield on JPM's actual HQLA book (see PROJECT NOTES below).
    """
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=281_117),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=677_995),
        Asset("Eligible Level 2A securities (GSE agency MBS)", "L2A", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=2_867),
        # JPM reported $0 average eligible Level 2B HQLA for 4Q25 -- omitted
        # rather than included as a zero-available line, since data.py's
        # Asset.available=0 would be indistinguishable from "not disclosed."
    ]


# Reported top-line summary [LCR p.1-2], for validating that
# net_cash_outflows(build_outflow_profile_jpm_4q25(), build_inflow_profile_jpm_4q25())
# reproduces JPM's own arithmetic. See limitation 3 above for the one
# reconciling item (maturity mismatch add-on) this project does not model.
JPM_4Q25_LCR_DISCLOSED = {
    "eligible_hqla_mm": 961_979,
    "total_cash_outflow_mm": 1_283_264,
    "total_cash_inflow_mm": 453_693,
    "inflow_cap_mm": 0.75 * 1_283_264,  # 962,448; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 829_571,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 38_929,  # NOT modeled -- see limitation 3
    "total_net_cash_outflow_mm": 868_500,  # = 829,571 + 38,929
    "lcr_pct": 111.0,
    "excess_eligible_hqla_mm": 93_479,
}

# "Other liquidity sources" [4Q25 LCR p.3, "Other liquidity sources"
# section] -- real resources JPM discloses but explicitly does NOT count
# toward eligible HQLA (that's the whole reason they're reported in a
# separate section). Precision note: unlike the LCR disclosure table above
# (exact to the $mm), JPM states these as "approximately $X billion," so
# they're rounded to the nearest $1,000mm here, not independently precise.
# Used only by model.py's infeasibility diagnostic (OptimizerConfig.
# contingent_liquidity_mm) -- never counted toward the LCR numerator itself.
JPM_4Q25_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 548_000,          # approx, fair value of unencumbered marketable securities not in eligible HQLA
    "fhlb_discount_window_capacity_mm": 449_000,    # approx, FHLB + Fed discount window borrowing capacity
    "total_mm": 548_000 + 449_000,
}


# ===========================================================================
# 1Q26 (quarter ended March 31, 2026) -- second real data point.
# ===========================================================================
# Source: JPMorganChase, "Liquidity Coverage Ratio Disclosure," quarterly
# period ended March 31, 2026 ("1Q26 LCR Report"). Same structure, same
# limitations (see module docstring) as the 4Q25 block above -- kept as an
# independent quarter rather than replacing 4Q25, so the two can be compared
# (e.g. to see how JPM's own outflow mix/HQLA composition shifts quarter to
# quarter) or used to sanity-check the optimizer against two different real
# NCO/HQLA levels instead of just one.
#
# Notable quarter-over-quarter change worth flagging: JPM reported $0
# average eligible Level 2A HQLA in 1Q26 (vs. $2,867mm in 4Q25) -- the whole
# $942,409mm of eligible HQLA this quarter was Level 1. Not a data-entry
# omission; that's what the source disclosure states [1Q26 LCR p.2].

def build_outflow_profile_jpm_1q26() -> list[OutflowLine]:
    """Real JPM outflow categories [1Q26 LCR p.2, "CASH OUTFLOW AMOUNTS" table]."""
    return [
        OutflowLine("Stable retail deposit outflow", "jpm_1q26_stable_retail_deposits", 681_976),
        OutflowLine("Other retail funding outflow", "jpm_1q26_other_retail_funding", 424_121),
        OutflowLine("Brokered deposit outflow", "jpm_1q26_brokered_deposits", 75_870),
        OutflowLine("Operational deposit outflow", "jpm_1q26_operational_deposits", 845_042),
        OutflowLine("Non-operational wholesale funding outflow", "jpm_1q26_nonoperational_wholesale_funding", 532_841),
        OutflowLine("Unsecured debt outflow", "jpm_1q26_unsecured_debt", 9_977),
        OutflowLine("Secured wholesale funding & asset exchange outflow", "jpm_1q26_secured_wholesale_funding", 1_267_571),
        OutflowLine("Derivative exposure & collateral outflow", "jpm_1q26_derivative_outflow", 113_359),
        OutflowLine("Credit & liquidity facility drawdown outflow", "jpm_1q26_credit_liquidity_facilities", 611_832),
        OutflowLine("Other contractual funding obligation outflow", "jpm_1q26_other_contractual_funding", 3_747),
        OutflowLine("Other contingent funding obligation outflow", "jpm_1q26_other_contingent_funding", 437_601),
    ]


def build_inflow_profile_jpm_1q26() -> list[InflowLine]:
    """Real JPM inflow categories [1Q26 LCR p.2, "CASH INFLOW AMOUNTS" table]."""
    return [
        InflowLine("Secured lending & asset exchange inflow", "jpm_1q26_secured_lending_inflow", 1_164_549),
        InflowLine("Retail cash inflow", "jpm_1q26_retail_inflow", 36_595),
        InflowLine("Unsecured wholesale cash inflow", "jpm_1q26_unsecured_wholesale_inflow", 40_532),
        InflowLine("Net derivative cash inflow", "jpm_1q26_derivative_inflow", 15_178),
        InflowLine("Securities cash inflow", "jpm_1q26_securities_inflow", 5_965),
        InflowLine("Broker-dealer segregated account inflow", "jpm_1q26_broker_dealer_segregated_inflow", 36_956),
        InflowLine("Other cash inflow", "jpm_1q26_other_inflow", 950),
    ]


def build_hqla_universe_jpm_1q26() -> list[Asset]:
    """
    Real JPM average eligible HQLA [1Q26 LCR p.3]. All Level 1 this
    quarter -- $0 average eligible Level 2A and Level 2B. See module
    docstring limitation 1 for why this is only 2 lines, not an instrument
    universe.
    """
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=258_543),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=683_866),
        # JPM reported $0 average eligible Level 2A and Level 2B HQLA for
        # 1Q26 -- omitted rather than included as zero-available lines, for
        # the same reason noted in build_hqla_universe_jpm_4q25().
    ]


# Reported top-line summary [1Q26 LCR p.1-2], same validation role as
# JPM_4Q25_LCR_DISCLOSED above.
JPM_1Q26_LCR_DISCLOSED = {
    "eligible_hqla_mm": 942_409,
    "total_cash_outflow_mm": 1_295_920,
    "total_cash_inflow_mm": 494_855,
    "inflow_cap_mm": 0.75 * 1_295_920,  # 971,940; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 801_065,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 43_840,  # NOT modeled -- see limitation 3
    "total_net_cash_outflow_mm": 844_905,  # = 801,065 + 43,840
    "lcr_pct": 112.0,
    "excess_eligible_hqla_mm": 97_504,
}

# "Other liquidity sources" [1Q26 LCR p.3]. See JPM_4Q25_OTHER_LIQUIDITY_SOURCES
# for the precision caveat (approximate, nearest $1,000mm).
JPM_1Q26_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 565_000,
    "fhlb_discount_window_capacity_mm": 450_000,
    "total_mm": 565_000 + 450_000,
}


# ===========================================================================
# 2Q25 (quarter ended June 30, 2025).
# ===========================================================================
# Source: "Liquidity Coverage Ratio Disclosure," quarterly period ended
# June 30, 2025 ("2Q25 LCR Report"). Same structure/limitations as 4Q25.
# Notable: $0 average eligible Level 2A and Level 2B HQLA this quarter --
# same pattern as 1Q26, different from 4Q25/3Q25 which both had a small
# nonzero Level 2A balance.

def build_outflow_profile_jpm_2q25() -> list[OutflowLine]:
    """Real JPM outflow categories [2Q25 LCR p.2, "CASH OUTFLOW AMOUNTS" table]."""
    return [
        OutflowLine("Stable retail deposit outflow", "jpm_2q25_stable_retail_deposits", 673_954),
        OutflowLine("Other retail funding outflow", "jpm_2q25_other_retail_funding", 401_086),
        OutflowLine("Brokered deposit outflow", "jpm_2q25_brokered_deposits", 97_170),
        OutflowLine("Operational deposit outflow", "jpm_2q25_operational_deposits", 777_558),
        OutflowLine("Non-operational wholesale funding outflow", "jpm_2q25_nonoperational_wholesale_funding", 519_279),
        OutflowLine("Unsecured debt outflow", "jpm_2q25_unsecured_debt", 8_464),
        OutflowLine("Secured wholesale funding & asset exchange outflow", "jpm_2q25_secured_wholesale_funding", 1_094_390),
        OutflowLine("Derivative exposure & collateral outflow", "jpm_2q25_derivative_outflow", 106_996),
        OutflowLine("Credit & liquidity facility drawdown outflow", "jpm_2q25_credit_liquidity_facilities", 567_135),
        OutflowLine("Other contractual funding obligation outflow", "jpm_2q25_other_contractual_funding", 4_294),
        OutflowLine("Other contingent funding obligation outflow", "jpm_2q25_other_contingent_funding", 419_137),
    ]


def build_inflow_profile_jpm_2q25() -> list[InflowLine]:
    """Real JPM inflow categories [2Q25 LCR p.2, "CASH INFLOW AMOUNTS" table]."""
    return [
        InflowLine("Secured lending & asset exchange inflow", "jpm_2q25_secured_lending_inflow", 1_047_050),
        InflowLine("Retail cash inflow", "jpm_2q25_retail_inflow", 31_532),
        InflowLine("Unsecured wholesale cash inflow", "jpm_2q25_unsecured_wholesale_inflow", 35_337),
        InflowLine("Net derivative cash inflow", "jpm_2q25_derivative_inflow", 18_608),
        InflowLine("Securities cash inflow", "jpm_2q25_securities_inflow", 6_944),
        InflowLine("Broker-dealer segregated account inflow", "jpm_2q25_broker_dealer_segregated_inflow", 31_562),
        InflowLine("Other cash inflow", "jpm_2q25_other_inflow", 0),
    ]


def build_hqla_universe_jpm_2q25() -> list[Asset]:
    """Real JPM average eligible HQLA [2Q25 LCR p.3]. All Level 1 -- $0
    average eligible Level 2A and Level 2B this quarter."""
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=349_403),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=572_533),
        # JPM reported $0 average eligible Level 2A and Level 2B HQLA for
        # 2Q25 -- omitted rather than included as zero-available lines.
    ]


JPM_2Q25_LCR_DISCLOSED = {
    "eligible_hqla_mm": 921_936,
    "total_cash_outflow_mm": 1_178_232,
    "total_cash_inflow_mm": 408_707,
    "inflow_cap_mm": 0.75 * 1_178_232,  # 883,674; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 769_525,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 48_809,  # NOT modeled -- see limitation 3
    "total_net_cash_outflow_mm": 818_334,  # = 769,525 + 48,809
    "lcr_pct": 113.0,
    "excess_eligible_hqla_mm": 103_602,
}

# "Other liquidity sources" [2Q25 LCR p.3]. See JPM_4Q25_OTHER_LIQUIDITY_SOURCES
# for the precision caveat (approximate, nearest $1,000mm).
JPM_2Q25_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 573_000,
    "fhlb_discount_window_capacity_mm": 422_000,
    "total_mm": 573_000 + 422_000,
}


# ===========================================================================
# 3Q25 (quarter ended September 30, 2025).
# ===========================================================================
# Source: "Liquidity Coverage Ratio Disclosure," quarterly period ended
# September 30, 2025 ("3Q25 LCR Report"). Same structure/limitations as
# 4Q25. Like 4Q25, this quarter has a small nonzero Level 2A balance
# (unlike 2Q25/1Q26, which reported $0 Level 2A).

def build_outflow_profile_jpm_3q25() -> list[OutflowLine]:
    """Real JPM outflow categories [3Q25 LCR p.2, "CASH OUTFLOW AMOUNTS" table]."""
    return [
        OutflowLine("Stable retail deposit outflow", "jpm_3q25_stable_retail_deposits", 670_253),
        OutflowLine("Other retail funding outflow", "jpm_3q25_other_retail_funding", 400_388),
        OutflowLine("Brokered deposit outflow", "jpm_3q25_brokered_deposits", 95_724),
        OutflowLine("Operational deposit outflow", "jpm_3q25_operational_deposits", 815_134),
        OutflowLine("Non-operational wholesale funding outflow", "jpm_3q25_nonoperational_wholesale_funding", 503_800),
        OutflowLine("Unsecured debt outflow", "jpm_3q25_unsecured_debt", 7_713),
        OutflowLine("Secured wholesale funding & asset exchange outflow", "jpm_3q25_secured_wholesale_funding", 1_137_507),
        OutflowLine("Derivative exposure & collateral outflow", "jpm_3q25_derivative_outflow", 102_039),
        OutflowLine("Credit & liquidity facility drawdown outflow", "jpm_3q25_credit_liquidity_facilities", 588_185),
        OutflowLine("Other contractual funding obligation outflow", "jpm_3q25_other_contractual_funding", 3_283),
        OutflowLine("Other contingent funding obligation outflow", "jpm_3q25_other_contingent_funding", 431_563),
    ]


def build_inflow_profile_jpm_3q25() -> list[InflowLine]:
    """Real JPM inflow categories [3Q25 LCR p.2, "CASH INFLOW AMOUNTS" table]."""
    return [
        InflowLine("Secured lending & asset exchange inflow", "jpm_3q25_secured_lending_inflow", 1_045_933),
        InflowLine("Retail cash inflow", "jpm_3q25_retail_inflow", 32_886),
        InflowLine("Unsecured wholesale cash inflow", "jpm_3q25_unsecured_wholesale_inflow", 34_447),
        InflowLine("Net derivative cash inflow", "jpm_3q25_derivative_inflow", 12_927),
        InflowLine("Securities cash inflow", "jpm_3q25_securities_inflow", 6_275),
        InflowLine("Broker-dealer segregated account inflow", "jpm_3q25_broker_dealer_segregated_inflow", 28_327),
        InflowLine("Other cash inflow", "jpm_3q25_other_inflow", 288),
    ]


def build_hqla_universe_jpm_3q25() -> list[Asset]:
    """Real JPM average eligible HQLA [3Q25 LCR p.3]."""
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=308_298),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=637_138),
        Asset("Eligible Level 2A securities (GSE agency MBS)", "L2A", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=1_038),
        # JPM reported $0 average eligible Level 2B HQLA for 3Q25 -- omitted.
    ]


JPM_3Q25_LCR_DISCLOSED = {
    "eligible_hqla_mm": 946_318,
    "total_cash_outflow_mm": 1_225_859,
    "total_cash_inflow_mm": 406_620,
    "inflow_cap_mm": 0.75 * 1_225_859,  # 919,394; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 819_239,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 38_918,  # NOT modeled -- see limitation 3
    "total_net_cash_outflow_mm": 858_157,  # = 819,239 + 38,918
    "lcr_pct": 110.0,
    "excess_eligible_hqla_mm": 88_161,
}

# "Other liquidity sources" [3Q25 LCR p.3]. See JPM_4Q25_OTHER_LIQUIDITY_SOURCES
# for the precision caveat (approximate, nearest $1,000mm).
JPM_3Q25_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 554_000,
    "fhlb_discount_window_capacity_mm": 444_000,
    "total_mm": 554_000 + 444_000,
}


# ===========================================================================
# Period registry -- lets a caller (dashboard, CLI, tests) pick a real
# quarter by key instead of importing each build_*_jpm_XqYY function by
# name. Add a new entry here whenever another quarter's LCR disclosure is
# extracted into this module.
# ===========================================================================
JPM_PERIODS = {
    "jpm_2q25": {
        "label": "JPMorgan Chase -- 2Q25 (real, quarter ended Jun 30, 2025)",
        "build_outflows": build_outflow_profile_jpm_2q25,
        "build_inflows": build_inflow_profile_jpm_2q25,
        "build_hqla": build_hqla_universe_jpm_2q25,
        "disclosed": JPM_2Q25_LCR_DISCLOSED,
        "other_liquidity_sources": JPM_2Q25_OTHER_LIQUIDITY_SOURCES,
    },
    "jpm_3q25": {
        "label": "JPMorgan Chase -- 3Q25 (real, quarter ended Sep 30, 2025)",
        "build_outflows": build_outflow_profile_jpm_3q25,
        "build_inflows": build_inflow_profile_jpm_3q25,
        "build_hqla": build_hqla_universe_jpm_3q25,
        "disclosed": JPM_3Q25_LCR_DISCLOSED,
        "other_liquidity_sources": JPM_3Q25_OTHER_LIQUIDITY_SOURCES,
    },
    "jpm_4q25": {
        "label": "JPMorgan Chase -- 4Q25 (real, quarter ended Dec 31, 2025)",
        "build_outflows": build_outflow_profile_jpm_4q25,
        "build_inflows": build_inflow_profile_jpm_4q25,
        "build_hqla": build_hqla_universe_jpm_4q25,
        "disclosed": JPM_4Q25_LCR_DISCLOSED,
        "other_liquidity_sources": JPM_4Q25_OTHER_LIQUIDITY_SOURCES,
    },
    "jpm_1q26": {
        "label": "JPMorgan Chase -- 1Q26 (real, quarter ended Mar 31, 2026)",
        "build_outflows": build_outflow_profile_jpm_1q26,
        "build_inflows": build_inflow_profile_jpm_1q26,
        "build_hqla": build_hqla_universe_jpm_1q26,
        "disclosed": JPM_1Q26_LCR_DISCLOSED,
        "other_liquidity_sources": JPM_1Q26_OTHER_LIQUIDITY_SOURCES,
    },
}


def build_hybrid_asset_universe(period_key: str, market_yields: dict | None = None) -> list[Asset]:
    """
    Real JPM tier-level HQLA capacity + the synthetic instrument-level
    universe from data.py -- the "replace what's real, keep what's
    essential-but-unpublished" split for the asset side of the model.

    WHY hybrid, not fully real: JPM's public LCR disclosure gives real
    dollar totals for HQLA by tier -- cash, Level 1 securities, Level 2A
    securities (see build_hqla_universe_jpm_*) -- but, like every bank,
    does not disclose which specific bonds/issuers make up that total; a
    per-instrument breakdown is confidential/supervisory information no
    bank publishes. The optimizer needs *some* multi-instrument structure
    (distinct issuers, yields, min lots) to make a meaningful allocation
    decision and to exercise the single-issuer concentration cap -- that
    structure is necessarily illustrative, carried over from data.py's
    synthetic universe, but rescaled here so each tier's *total available
    capacity* matches JPM's real disclosed number for the quarter, not the
    original synthetic bank's made-up ~$50bn scale. Relative weights
    between instruments within a tier come from the synthetic universe
    (there's no real basis to prefer one over another); the tier totals
    they sum to are real.

    What's real, unchanged:
      - Cash: kept at its literal disclosed value. "Cash on deposit at
        central banks" isn't a diversified instrument category to split,
        so there's nothing to rescale.
      - Every non-cash instrument's *available* is rescaled so its tier
        sums to JPM's real disclosed L1-securities / L2A total for the
        quarter (verified in the test suite to reproduce the disclosed
        total almost exactly).
      - Level 2B instruments are dropped entirely for both 4Q25 and 1Q26,
        because JPM's real disclosure reports $0 average eligible Level 2B
        HQLA in both quarters -- carrying a nonzero synthetic Level 2B
        line here would misrepresent what JPM actually held.

    What's kept synthetic, deliberately:
      - Instrument names/issuers/yields within each tier (no public source
        for JPM's actual holdings at that granularity).
      - Non-HQLA instruments, unchanged from data.py: their only role is
        to verify the solver correctly excludes them from the LCR
        numerator, which doesn't depend on any real JPM quantity.
    """
    real_hqla = JPM_PERIODS[period_key]["build_hqla"]()
    real_cash = next(a.available for a in real_hqla if "cash" in a.name.lower())
    real_l1_securities = sum(a.available for a in real_hqla if a.tier == "L1" and "cash" not in a.name.lower())
    real_l2a = sum(a.available for a in real_hqla if a.tier == "L2A")

    template = build_asset_universe(market_yields=market_yields)

    result = []
    for a in template:
        if a.tier == "L1" and "Cash" in a.name:
            result.append(replace(a, available=real_cash))
        elif a.tier in ("L2B_RMBS", "L2B_CORP", "L2B_EQUITY"):
            continue  # real disclosure shows $0 Level 2B this quarter -- omit rather than fake availability
        elif a.tier not in ("L1", "L2A"):
            result.append(a)  # non_hqla passthrough, unchanged synthetic

    l1_noncash = [a for a in template if a.tier == "L1" and "Cash" not in a.name]
    l1_noncash_total = sum(a.available for a in l1_noncash)
    for a in l1_noncash:
        share = a.available / l1_noncash_total if l1_noncash_total else 0.0
        result.append(replace(a, available=real_l1_securities * share))

    l2a_instruments = [a for a in template if a.tier == "L2A"]
    l2a_total = sum(a.available for a in l2a_instruments)
    for a in l2a_instruments:
        share = a.available / l2a_total if l2a_total else 0.0
        result.append(replace(a, available=real_l2a * share))

    return result
