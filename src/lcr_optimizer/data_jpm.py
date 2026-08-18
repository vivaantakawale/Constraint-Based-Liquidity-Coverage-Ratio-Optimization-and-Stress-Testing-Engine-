"""
Real JPMorgan Chase balance sheet / LCR data, from public filings
Companion to data.py's synthetic bank

Sources ($ millions):
  [LCR] JPMorganChase "Liquidity Coverage Ratio Disclosure," quarter ended 12/31/2025
        Required filing, US LCR rule (12 CFR 249 Subpart R)
        Average balances/weighted amounts for quarter
  [P3]  JPMorganChase "Pillar 3 Regulatory Capital Disclosures," 4Q25
        Capital side only (CET1, RWA, SLR); cross-reference for capital.py

LIMITATIONS:
1. Aggregate only, not instrument level
   JPM discloses ~11-20 category buckets, not per instrument/counterparty (confidential)
2. No Level 2B
   JPM reported $0 average eligible Level 2B HQLA every quarter extracted
3. Maturity mismatch add on NOT modeled
   JPM's Total NCO = sum-of-categories
   NCO (matches net_cash_outflows() exactly) + a US-rule-specific timing mismatch add-on with no BCBS238 equivalent
   Computed LCR therefore reads a few points higher than JPM's reported figure
4. Rates blended/implied, not structural
   Each rate is realized weighted average across many counterparties/products, not a single BCBS238 paragraph rate
"""

from dataclasses import replace

from .data import Asset, InflowLine, OutflowLine, build_asset_universe


def build_outflow_profile_jpm_4q25() -> list[OutflowLine]:
    """
    Real JPM outflow categories [LCR p.2]
    ARGS: None
    RETURNS: list[OutflowLine], 11 lines, 4Q25
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
    """
    Real JPM inflow categories [LCR p.2]
    ARGS: None 
    RETURNS: list[InflowLine], 7 lines, 4Q25
    """
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
    Real JPM average eligible HQLA [LCR p.3] 
    Only 3 lines publicly disclosed (see limitation 1)
    ARGS: None 
    RETURNS: list[Asset], 3 lines, 4Q25
    """
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=281_117),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=677_995),
        Asset("Eligible Level 2A securities (GSE agency MBS)", "L2A", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=2_867),
        # JPM reported $0 average eligible Level 2B HQLA for 4Q25 omitted rather than included as zero available line
        # since data.py's Asset.available=0 would be indistinguishable from "not disclosed"
    ]


# Reported top line summary [LCR p.1-2], for validating that net_cash_outflows(build_outflow_profile_jpm_4q25(), build_inflow_profile_jpm_4q25())
# reproduces JPM's own arithmetic
# See limitation 3 above for reconciling item (maturity mismatch add-on) this project does not model
JPM_4Q25_LCR_DISCLOSED = {
    "eligible_hqla_mm": 961_979,
    "total_cash_outflow_mm": 1_283_264,
    "total_cash_inflow_mm": 453_693,
    "inflow_cap_mm": 0.75 * 1_283_264,  # 962,448; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 829_571,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 38_929,  # NOT modeled (see limitation 3)
    "total_net_cash_outflow_mm": 868_500,  # = 829,571 + 38,929
    "lcr_pct": 111.0,
    "excess_eligible_hqla_mm": 93_479,
}

# "Other liquidity sources" [4Q25 LCR p.3, "Other liquidity sources" section]
# real resources JPM discloses but explicitly does NOT count toward eligible HQLA
# Precision note: unlike LCR disclosure table above (exact to the $mm), JPM states these as "approximately $X billion," 
# thus rounded to nearest $1,000mm here, not independently precise
# Used only by model.py's infeasibility diagnostic (OptimizerConfig.contingent_liquidity_mm) never counted toward the LCR numerator itself
JPM_4Q25_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 548_000,          # approx, fair value of unencumbered marketable securities not in eligible HQLA
    "fhlb_discount_window_capacity_mm": 449_000,    # approx, FHLB + Fed discount window borrowing capacity
    "total_mm": 548_000 + 449_000,
}


# 1Q26 (quarter ended March 31, 2026)

# Source: "Liquidity Coverage Ratio Disclosure," quarter ended 3/31/2026
# Same structure/limitations as 4Q25 block
# Notable: $0 average eligible Level 2A HQLA this quarter (vs. $2,867mm in 4Q25) 
# whole $942,409mm was Level 1
# Real disclosed figure, not omission

def build_outflow_profile_jpm_1q26() -> list[OutflowLine]:
    """
    Real JPM outflow categories [1Q26 LCR p.2] 
    ARGS: None
    RETURNS: list[OutflowLine], 11 lines, 1Q26
    """
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
    """
    Real JPM inflow categories [1Q26 LCR p.2]
    ARGS: None 
    RETURNS: list[InflowLine], 7 lines, 1Q26
    """
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
    Real JPM average eligible HQLA [1Q26 LCR p.3] All Level 1 $0 average eligible Level 2A/2B
    See limitation 1
    ARGS: None
    RETURNS: list[Asset], 2 lines, 1Q26 (cash, L1 securities)
    """
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=258_543),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=683_866),
        # JPM reported $0 average eligible Level 2A and Level 2B HQLA for 1Q26 omitted rather than included as zero available lines
        # same reason noted in build_hqla_universe_jpm_4q25()
    ]


# Reported top line summary [1Q26 LCR p.1-2], same validation role as JPM_4Q25_LCR_DISCLOSED above
JPM_1Q26_LCR_DISCLOSED = {
    "eligible_hqla_mm": 942_409,
    "total_cash_outflow_mm": 1_295_920,
    "total_cash_inflow_mm": 494_855,
    "inflow_cap_mm": 0.75 * 1_295_920,  # 971,940; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 801_065,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 43_840,  # NOT modeled (see limitation 3)
    "total_net_cash_outflow_mm": 844_905,  # = 801,065 + 43,840
    "lcr_pct": 112.0,
    "excess_eligible_hqla_mm": 97_504,
}

# "Other liquidity sources" [1Q26 LCR p.3] 
# See JPM_4Q25_OTHER_LIQUIDITY_SOURCES for the precision caveat (approximate, nearest $1,000mm)
JPM_1Q26_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 565_000,
    "fhlb_discount_window_capacity_mm": 450_000,
    "total_mm": 565_000 + 450_000,
}


# 2Q25 (quarter ended June 30, 2025)

 # Source: "Liquidity Coverage Ratio Disclosure," quarter ended 6/30/2025
# Same structure/limitations as 4Q25
# Notable: $0 Level 2A/2B this quarter same pattern as 1Q26, unlike 4Q25/3Q25's small nonzero Level 2A

def build_outflow_profile_jpm_2q25() -> list[OutflowLine]:
    """
    Real JPM outflow categories [2Q25 LCR p.2]
    ARGS: None
    RETURNS: list[OutflowLine], 11 lines, 2Q25
    """
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
    """
    Real JPM inflow categories [2Q25 LCR p.2] 
    ARGS: None 
    RETURNS: list[InflowLine], 7 lines, 2Q25
    """
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
    """
    Real JPM average eligible HQLA [2Q25 LCR p.3] 
    All Level 1 - $0 Level 2A/2B
    ARGS: None 
    RETURNS: list[Asset], 2 lines, 2Q25 (cash, L1 securities)
    """
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=349_403),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=572_533),
        # JPM reported $0 average eligible Level 2A and Level 2B HQLA for 2Q25 
    ]


JPM_2Q25_LCR_DISCLOSED = {
    "eligible_hqla_mm": 921_936,
    "total_cash_outflow_mm": 1_178_232,
    "total_cash_inflow_mm": 408_707,
    "inflow_cap_mm": 0.75 * 1_178_232,  # 883,674; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 769_525,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 48_809,  # NOT modeled (see limitation 3)
    "total_net_cash_outflow_mm": 818_334,  # = 769,525 + 48,809
    "lcr_pct": 113.0,
    "excess_eligible_hqla_mm": 103_602,
}

# "Other liquidity sources" [2Q25 LCR p.3]
# See JPM_4Q25_OTHER_LIQUIDITY_SOURCES for the precision caveat (approximate, nearest $1,000mm)
JPM_2Q25_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 573_000,
    "fhlb_discount_window_capacity_mm": 422_000,
    "total_mm": 573_000 + 422_000,
}


# 3Q25 (quarter ended September 30, 2025)

# Source: "Liquidity Coverage Ratio Disclosure," quarter ended 9/30/2025
# Same structure/limitations as 4Q25
# Small nonzero Level 2A balance, like 4Q25 (unlike 2Q25/1Q26's $0 Level 2A)

def build_outflow_profile_jpm_3q25() -> list[OutflowLine]:
    """
    Real JPM outflow categories [3Q25 LCR p.2]
    ARGS: None
    RETURNS: list[OutflowLine], 11 lines, 3Q25
    """
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
    """
    Real JPM inflow categories [3Q25 LCR p.2] 
    ARGS: None
    RETURNS: list[InflowLine], 7 lines, 3Q25"""
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
    """
    Real JPM average eligible HQLA [3Q25 LCR p.3]
    ARGS: None
    RETURNS: list[Asset], 3 lines, 3Q25 (cash, L1 securities, L2A securities)
    """
    return [
        Asset("Eligible cash (central bank deposits)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=308_298),
        Asset("Eligible Level 1 securities (UST, agency MBS, sovereign)", "L1", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=637_138),
        Asset("Eligible Level 2A securities (GSE agency MBS)", "L2A", "JPM_actual", 0.0, "unknown",
              min_lot=1.0, available=1_038),
        # JPM reported $0 average eligible Level 2B HQLA for 3Q25 (omitted)
    ]


JPM_3Q25_LCR_DISCLOSED = {
    "eligible_hqla_mm": 946_318,
    "total_cash_outflow_mm": 1_225_859,
    "total_cash_inflow_mm": 406_620,
    "inflow_cap_mm": 0.75 * 1_225_859,  # 919,394; uncapped inflow is below the cap, so unused here
    "net_cash_outflow_excl_maturity_mismatch_mm": 819_239,  # matches net_cash_outflows() exactly
    "maturity_mismatch_addon_mm": 38_918,  # NOT modeled (see limitation 3)
    "total_net_cash_outflow_mm": 858_157,  # = 819,239 + 38,918
    "lcr_pct": 110.0,
    "excess_eligible_hqla_mm": 88_161,
}

# "Other liquidity sources" [3Q25 LCR p.3]
# See JPM_4Q25_OTHER_LIQUIDITY_SOURCES for the precision caveat (approximate, nearest $1,000mm)
JPM_3Q25_OTHER_LIQUIDITY_SOURCES = {
    "unencumbered_securities_mm": 554_000,
    "fhlb_discount_window_capacity_mm": 444_000,
    "total_mm": 554_000 + 444_000,
}


# Period registry
# lets caller (dashboard, CLI, tests) pick real quarter by key instead of importing each build_*_jpm_XqYY function by name

# Type: dict[str, dict] keyed by period key
# Value dict:
#   "label": str  "build_outflows"/"build_inflows"/"build_hqla": Callable[[], list]
#   "disclosed": dict[str,float]  "other_liquidity_sources": dict[str,float]

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
    Real JPM tier level HQLA capacity + synthetic instrument level universe from data.py

    WHY hybrid: JPM discloses real dollar totals per tier, not per-instrument (confidential for every bank)
    Optimizer needs multi instrument structure for meaningful allocation + concentration cap
    so synthetic instrument set is rescaled per tier to match real totals
    relative weights within tier stay synthetic, tier totals are real

    Real & unchanged: cash at literal disclosed value
    Rescaled: every non-cash instrument's `available`, per-tier, to match real L1/L2A total
    Dropped: Level 2B instruments (real disclosure shows $0 every quarter extracted; drop is quarter-agnostic, keyed off tier)
    Kept synthetic: instrument names/issuers/yields (no public per-instrument source), non-HQLA instruments (unchanged from data.py)

    ARGS: period_key: str JPM_PERIODS key, raises KeyError if absent
      market_yields: dict[str,float]|None passed through to data.build_asset_universe()
    Returns: list[Asset] synthetic instruments, `available` rescaled per-tier to real totals, Level 2B dropped
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
            continue  # real disclosure shows $0 Level 2B this quarter (omit)
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
