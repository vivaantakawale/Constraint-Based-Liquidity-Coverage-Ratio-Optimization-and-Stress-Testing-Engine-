"""
Synthetic bank balance sheet + HQLA asset universe
Not real data magnitudes shaped like ~$50bn regional bank, dollar figures made up
Rates (run-off/inflow/haircut) are real BCBS238 figures from rates.py
Asset yields: live where noted (market_data.py) else hardcoded/representative
"""

from dataclasses import dataclass
from typing import Optional

from . import rates


@dataclass
class OutflowLine:
    """Synthetic outflow category line
    name: str label
    category: str key into rates.OUTFLOW_RATES
    balance: float $mm, unweighted
    """
    name: str
    category: str
    balance: float

    @property
    def rate(self) -> float:
        """Run off rate for category
        RETURNS: float
        """
        return rates.OUTFLOW_RATES[self.category]

    @property
    def stressed_outflow(self) -> float:
        """RETURNS: float $mm = balance * rate"""
        return self.balance * self.rate


@dataclass
class InflowLine:
    """Synthetic inflow category line
    Mirrors OutflowLine
    name: str label
    category: str key into rates.INFLOW_RATES
    balance: float $mm, unweighted
    """
    name: str
    category: str
    balance: float

    @property
    def rate(self) -> float:
        """Inflow rate for category
        RETURNS: float"""
        return rates.INFLOW_RATES[self.category]

    @property
    def stressed_inflow(self) -> float:
        """RETURNS: float $mm = balance * rate"""
        return self.balance * self.rate


@dataclass
class Asset:
    """Candidate HQLA universe instrument; unit model.py's optimizer sizes
    name: str label
    tier: str "L1"/"L2A"/"L2B_RMBS"/"L2B_CORP"/"L2B_EQUITY"/"non_hqla"
    issuer: str, for concentration cap
    yield_pct: float decimal annualized yield
    yield_source: str "live"/"synthetic"/"fixed"/"unknown"
    min_lot: float $mm, default 1.0
    available: float|None max $mm obtainable; inf if unbounded
    """
    name: str
    tier: str
    issuer: str
    yield_pct: float
    yield_source: str
    min_lot: float = 1.0
    available: Optional[float] = None

    @property
    def haircut(self) -> float:
        """Tier haircut
        RETURNS: float, or None if non_hqla
        """
        if self.tier == "non_hqla":
            return None
        return rates.HAIRCUTS[self.tier]

    @property
    def is_hqla(self) -> bool:
        """RETURNS: bool, False only for non_hqla"""
        return self.tier != "non_hqla"


def build_outflow_profile() -> list[OutflowLine]:
    """Synthetic ~$50bn regional-bank funding base
    ARGS: None
    RETURNS: list[OutflowLine], 14 lines
    """
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
    """Synthetic inflow profile, pairs with build_outflow_profile()
    ARGS: None
    RETURNS: list[InflowLine], 5 lines
    """
    return [
        InflowLine("Retail/SME loans maturing <=30d", "retail_sme_loans", 1_600),
        InflowLine("Corporate loans maturing <=30d", "wholesale_nonfinancial_loans", 2_000),
        InflowLine("Interbank placements maturing <=30d", "wholesale_financial_receivables", 900),
        InflowLine("Reverse repo vs Treasuries (L1 collateral)", "reverse_repo_L1_collateral", 1_200),
        InflowLine("Reverse repo vs agency/AA corp (L2A collateral)", "reverse_repo_L2A_collateral", 500),
    ]


def net_cash_outflows(outflows: list[OutflowLine], inflows: list[InflowLine]) -> dict:
    """
    BCBS238 para 69: NCO = Total stressed outflows - min(Total stressed inflows, 75% * Total stressed outflows)

    ARGS: outflows: list[OutflowLine], inflows: list[InflowLine] or duck-typed wrappers exposing .stressed_outflow/.stressed_inflow (e.g. scenario overrides)
    RETURNS: dict[str, float] $mm total_outflow, total_inflow_uncapped, inflow_cap, total_inflow_capped, net_cash_outflows.
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
    15-25 candidate instruments spanning HQLA tiers plus non-HQLA assets (proves optimizer correctly excludes them)

    ARGS: market_yields: dict[str, float]|None -- optional {"UST_1M": ..., "UST_2Y": ...} to override just two live-priced Treasuries; rest stay hardcoded
    RETURNS: list[Asset], 16 instruments (5 L1, 5 L2A, 4 L2B, 2 non_hqla)
    """
    my = market_yields or {}

    assets = [
        # Level 1: no haircut, unlimited, near zero yield
        # Cash is unbounded instrument: 
        # bank isn't constrained on how much it can hold at central bank, only cost constrained
        # (holding cash forgoes benchmark yield, so solver won't over hold it)
        Asset("Cash / central bank reserves", "L1", "central_bank", 0.000, "fixed", min_lot=1.0, available=float("inf")),
        Asset("US Treasury bill (1M)", "L1", "US Treasury", my.get("UST_1M", 0.042), "live", min_lot=1.0, available=5_000),
        Asset("US Treasury note (2Y)", "L1", "US Treasury", my.get("UST_2Y", 0.038), "live", min_lot=1.0, available=5_000),
        Asset("German Bund (2Y)", "L1", "Germany", 0.022, "synthetic", min_lot=1.0, available=2_000),
        Asset("UK Gilt (2Y)", "L1", "UK", 0.040, "synthetic", min_lot=1.0, available=1_500),

        # Level 2A: 15% haircut, AA-/better or 20%-RW sovereign/PSE 
        Asset("US Agency MBS (guaranteed)", "L2A", "GSE", 0.045, "synthetic", min_lot=5.0, available=2_000),
        Asset("French OAT (AA)", "L2A", "France", 0.030, "synthetic", min_lot=5.0, available=1_000),
        Asset("Microsoft corp bond (AAA)", "L2A", "Microsoft", 0.048, "synthetic", min_lot=5.0, available=250),
        Asset("Johnson & Johnson corp bond (AAA)", "L2A", "J&J", 0.047, "synthetic", min_lot=5.0, available=200),
        Asset("Covered bond (AA)", "L2A", "EU_bank_pool", 0.041, "synthetic", min_lot=5.0, available=800),

        # Level 2B: 25-50% haircut, capped at 15% of HQLA 
        Asset("Prime RMBS (AA)", "L2B_RMBS", "RMBS_pool", 0.052, "synthetic", min_lot=5.0, available=300),
        Asset("BBB corporate bond (industrial)", "L2B_CORP", "IndustrialCo", 0.061, "synthetic", min_lot=5.0, available=250),
        Asset("A- corporate bond (utility)", "L2B_CORP", "UtilityCo", 0.056, "synthetic", min_lot=5.0, available=250),
        Asset("Blue-chip equity index basket", "L2B_EQUITY", "EquityIndex", 0.070, "synthetic", min_lot=5.0, available=200),

        # Non-HQLA: ineligible; included so solver has to correctly exclude them Bounded 
        # (not left to fallback default) because HYCo's yield exceeds benchmark giving it negative opportunity cost 
        # finite supply limit required, since non HQLA assets don't interact with any LCR constraint
        Asset("Unrated private corporate loan participation", "non_hqla", "PrivateCo", 0.085, "synthetic", min_lot=5.0, available=150),
        Asset("High-yield (junk) corporate bond", "non_hqla", "HYCo", 0.095, "synthetic", min_lot=5.0, available=100),
    ]
    return assets


# Benchmark yield used for opportunity cost objective (see model.py):
# return bank forgoes by holding HQLA instead of its marginal lending/investment alternative
BENCHMARK_YIELD = 0.085
