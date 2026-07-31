"""
Synthetic bank balance sheet + HQLA asset universe.

Everything in this file is SYNTHETIC / illustrative -- it is not any real
bank's balance sheet. The categories and rough magnitudes are shaped like a
mid-size regional bank (~$50bn total assets) so the numbers "feel" realistic,
but every dollar figure is made up. The regulatory *rates* attached to each
category (run-off %, inflow %, haircuts) are the real BCBS238 figures from
rates.py -- that split (fake balance sheet, real regulatory treatment) is
called out explicitly per the project brief.

Asset yields: where noted, pulled from a live source in market_data.py
(falls back to a hardcoded snapshot if offline). Corporate/equity yields are
representative spreads over the treasury curve, not live-quoted -- also
labeled below.
"""

from dataclasses import dataclass, field
from typing import Optional

from . import rates


@dataclass
class OutflowLine:
    name: str
    category: str          # key into rates.OUTFLOW_RATES
    balance: float          # $mm, synthetic

    @property
    def rate(self) -> float:
        return rates.OUTFLOW_RATES[self.category]

    @property
    def stressed_outflow(self) -> float:
        return self.balance * self.rate


@dataclass
class InflowLine:
    name: str
    category: str          # key into rates.INFLOW_RATES
    balance: float          # $mm, synthetic

    @property
    def rate(self) -> float:
        return rates.INFLOW_RATES[self.category]

    @property
    def stressed_inflow(self) -> float:
        return self.balance * self.rate


@dataclass
class Asset:
    """One candidate instrument in the HQLA universe."""
    name: str
    tier: str               # "L1", "L2A", "L2B_RMBS", "L2B_CORP", "L2B_EQUITY", or "non_hqla"
    issuer: str              # for single-issuer concentration limits
    yield_pct: float          # annualized yield, decimal (e.g. 0.045)
    yield_source: str         # "live", "synthetic", or "fixed" (e.g. cash = 0%)
    min_lot: float = 1.0      # smallest increment the asset can be bought in, $mm
    available: Optional[float] = None  # max $mm obtainable in this instrument (float('inf') if unbounded)

    @property
    def haircut(self) -> float:
        if self.tier == "non_hqla":
            return None
        return rates.HAIRCUTS[self.tier]

    @property
    def is_hqla(self) -> bool:
        return self.tier != "non_hqla"


def build_outflow_profile() -> list[OutflowLine]:
    """A synthetic ~$50bn regional-bank funding base."""
    return [
        OutflowLine("Insured retail checking/savings", "retail_stable", 14_000),
        OutflowLine("Uninsured / less-sticky retail deposits", "retail_less_stable", 4_000),
        OutflowLine("Small-business operating deposits (stable)", "sme_stable", 2_500),
        OutflowLine("Small-business deposits (less stable)", "sme_less_stable", 1_200),
        OutflowLine("Corporate cash-management / custody balances", "operational_deposits", 3_000),
        OutflowLine("Non-financial corporate wholesale deposits", "wholesale_nonfinancial", 2_800),
        OutflowLine("Interbank / other-FI wholesale funding", "wholesale_financial", 1_500),
        OutflowLine("Repo funded against Treasuries (L1 collateral)", "secured_L1_collateral", 3_000),
        OutflowLine("Repo funded against agency/AA corp (L2A collateral)", "secured_L2A_collateral", 900),
        OutflowLine("Repo funded against non-HQLA collateral", "secured_other_collateral", 400),
        OutflowLine("Committed retail/SME credit lines", "facility_retail_sme", 1_800),
        OutflowLine("Committed corporate credit facilities", "facility_corporate_credit", 2_200),
        OutflowLine("Committed corporate liquidity backstops", "facility_corporate_liquidity", 900),
        OutflowLine("Committed facilities to other financial institutions", "facility_financial", 600),
    ]


def build_inflow_profile() -> list[InflowLine]:
    return [
        InflowLine("Retail/SME loans maturing <=30d", "retail_sme_loans", 1_600),
        InflowLine("Corporate loans maturing <=30d", "wholesale_nonfinancial_loans", 2_000),
        InflowLine("Interbank placements maturing <=30d", "wholesale_financial_receivables", 900),
        InflowLine("Reverse repo vs Treasuries (L1 collateral)", "reverse_repo_L1_collateral", 1_200),
        InflowLine("Reverse repo vs agency/AA corp (L2A collateral)", "reverse_repo_L2A_collateral", 500),
    ]


def net_cash_outflows(outflows: list[OutflowLine], inflows: list[InflowLine]) -> dict:
    """
    BCBS238 para 69: Net Cash Outflows = Total stressed outflows
                       - min(Total stressed inflows, 75% * Total stressed outflows)
    """
    total_outflow = sum(o.stressed_outflow for o in outflows)
    total_inflow_uncapped = sum(i.stressed_inflow for i in inflows)
    inflow_cap = rates.INFLOW_CAP_OF_OUTFLOWS * total_outflow
    total_inflow_capped = min(total_inflow_uncapped, inflow_cap)
    nco = total_outflow - total_inflow_capped
    return {
        "total_outflow": total_outflow,
        "total_inflow_uncapped": total_inflow_uncapped,
        "inflow_cap": inflow_cap,
        "total_inflow_capped": total_inflow_capped,
        "net_cash_outflows": nco,
    }


def build_asset_universe(market_yields: Optional[dict] = None) -> list[Asset]:
    """
    15-25 candidate instruments spanning the HQLA tiers plus a couple of
    non-HQLA assets (to show the optimizer correctly refuses to count them).

    market_yields: optional dict of {name: yield_pct} from market_data.py to
    override the hardcoded snapshot below with live-fetched numbers.
    """
    my = market_yields or {}

    assets = [
        # --- Level 1: no haircut, unlimited, near-zero yield ---
        # Cash is the one genuinely unbounded instrument: a bank isn't market-depth
        # constrained on how much it can hold at the central bank, only cost-constrained
        # (holding cash forgoes the benchmark yield, so the solver won't over-hold it).
        Asset("Cash / central bank reserves", "L1", "central_bank", 0.000, "fixed", min_lot=1.0, available=float("inf")),
        Asset("US Treasury bill (1M)", "L1", "US Treasury", my.get("UST_1M", 0.042), "live", min_lot=1.0, available=5_000),
        Asset("US Treasury note (2Y)", "L1", "US Treasury", my.get("UST_2Y", 0.038), "live", min_lot=1.0, available=5_000),
        Asset("German Bund (2Y)", "L1", "Germany", 0.022, "synthetic", min_lot=1.0, available=2_000),
        Asset("UK Gilt (2Y)", "L1", "UK", 0.040, "synthetic", min_lot=1.0, available=1_500),

        # --- Level 2A: 15% haircut, AA-/better or 20%-RW sovereign/PSE ---
        Asset("US Agency MBS (guaranteed)", "L2A", "GSE", 0.045, "synthetic", min_lot=5.0, available=2_000),
        Asset("French OAT (AA)", "L2A", "France", 0.030, "synthetic", min_lot=5.0, available=1_000),
        Asset("Microsoft corp bond (AAA)", "L2A", "Microsoft", 0.048, "synthetic", min_lot=5.0, available=250),
        Asset("Johnson & Johnson corp bond (AAA)", "L2A", "J&J", 0.047, "synthetic", min_lot=5.0, available=200),
        Asset("Covered bond (AA)", "L2A", "EU_bank_pool", 0.041, "synthetic", min_lot=5.0, available=800),

        # --- Level 2B: 25-50% haircut, capped at 15% of HQLA ---
        Asset("Prime RMBS (AA)", "L2B_RMBS", "RMBS_pool", 0.052, "synthetic", min_lot=5.0, available=300),
        Asset("BBB corporate bond (industrial)", "L2B_CORP", "IndustrialCo", 0.061, "synthetic", min_lot=5.0, available=250),
        Asset("A- corporate bond (utility)", "L2B_CORP", "UtilityCo", 0.056, "synthetic", min_lot=5.0, available=250),
        Asset("Blue-chip equity index basket", "L2B_EQUITY", "EquityIndex", 0.070, "synthetic", min_lot=5.0, available=200),

        # --- Non-HQLA: ineligible; included so the solver has to correctly exclude them.
        # Bounded (not left to a fallback default) because HYCo's yield exceeds the
        # benchmark, giving it *negative* opportunity cost -- without a finite supply
        # limit the solver would treat it as a free lunch and load up on it without
        # limit, since non-HQLA assets don't interact with any LCR constraint at all.
        Asset("Unrated private corporate loan participation", "non_hqla", "PrivateCo", 0.085, "synthetic", min_lot=5.0, available=150),
        Asset("High-yield (junk) corporate bond", "non_hqla", "HYCo", 0.095, "synthetic", min_lot=5.0, available=100),
    ]
    return assets


# Benchmark yield used for the opportunity-cost objective (see model.py):
# the return the bank forgoes by holding HQLA instead of its marginal
# lending/investment alternative. Synthetic, but in the range of a typical
# mid-size bank's loan book yield.
BENCHMARK_YIELD = 0.085
