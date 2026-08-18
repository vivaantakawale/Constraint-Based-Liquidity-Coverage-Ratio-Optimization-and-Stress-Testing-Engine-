"""
Stress scenario layer.

CAVEAT: BCBS238 rates already ARE a stress calibration -- Basel doesn't
publish a second "scenario B" rate set. Scenarios below extend the
regulatory baseline (like a bank's own internal liquidity stress test
would), not a BCBS-specified severity layer -- flag this if asked.

Tier haircuts (15%/25%/50%) NOT shocked -- fixed regulatory constants.
Only outflow/inflow rates overridden, matching Basel's own stress mechanism.
"""

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .data import build_inflow_profile, build_outflow_profile, net_cash_outflows
from .model import OptimizerConfig, solve

if TYPE_CHECKING:
    import pandas


@dataclass
class Scenario:
    """One named stress scenario, synthetic bank.
    name: str, label. description: str. outflow_rate_overrides: dict[str,float],
    category key -> replacement absolute rate, default {} (baseline).
    inflow_rate_overrides: dict[str,float], same shape.
    """
    name: str
    description: str
    outflow_rate_overrides: dict = field(default_factory=dict)
    inflow_rate_overrides: dict = field(default_factory=dict)


BASELINE = Scenario(
    name="Baseline",
    description="Standard BCBS238 run-off/inflow rates, no additional stress.",
)

DEPOSIT_RUN = Scenario(
    name="Retail Deposit Run",
    description=(
        "A depositor-confidence shock (social-media-driven run dynamics, e.g. SVB 2023): "
        "retail and small-business run-off rates roughly double vs. the BCBS238 baseline."
    ),
    outflow_rate_overrides={
        "retail_stable": 0.10,        # baseline 0.05
        "retail_less_stable": 0.25,   # baseline 0.10
        "sme_stable": 0.10,           # baseline 0.05
        "sme_less_stable": 0.25,      # baseline 0.10
    },
)

WHOLESALE_FREEZE = Scenario(
    name="Wholesale Funding Freeze",
    description=(
        "Interbank/repo markets seize up: secured funding against anything but Level 1 "
        "collateral can't be rolled, committed facilities get drawn, and wholesale "
        "counterparties can't be relied on to repay inflows on time."
    ),
    outflow_rate_overrides={
        "wholesale_nonfinancial": 0.75,      # baseline 0.40
        "secured_L2A_collateral": 0.50,       # baseline 0.15
        "facility_corporate_credit": 0.50,    # baseline 0.10
        "facility_corporate_liquidity": 1.00,  # baseline 0.30
        "facility_financial": 1.00,           # baseline 0.40
    },
    inflow_rate_overrides={
        "wholesale_nonfinancial_loans": 0.25,       # baseline 0.50
        "wholesale_financial_receivables": 0.50,     # baseline 1.00
    },
)

COMBINED_2008 = Scenario(
    name="Combined 2008-Style Shock",
    description=(
        "Deposit run + wholesale freeze simultaneously, with an extra severity increment "
        "on the two categories that drove the 2007-09 crisis hardest: uninsured retail/SME "
        "flight and non-financial wholesale withdrawal."
    ),
    outflow_rate_overrides={
        **DEPOSIT_RUN.outflow_rate_overrides,
        **WHOLESALE_FREEZE.outflow_rate_overrides,
        "retail_less_stable": 0.30,       # extra increment vs. DEPOSIT_RUN's 0.25
        "wholesale_nonfinancial": 0.85,   # extra increment vs. WHOLESALE_FREEZE's 0.75
    },
    inflow_rate_overrides={
        **WHOLESALE_FREEZE.inflow_rate_overrides,
    },
)

ALL_SCENARIOS = [BASELINE, DEPOSIT_RUN, WHOLESALE_FREEZE, COMBINED_2008]


class _OverriddenLine:
    """Wraps OutflowLine/InflowLine so `.rate` reflects scenario override,
    rest (name, balance) passthrough.
    Args (constructor): line: OutflowLine|InflowLine. overrides: dict[str,float].
    """
    def __init__(self, line, overrides):
        self._line = line
        self._overrides = overrides

    def __getattr__(self, item):
        """Passthrough for non-overridden attributes. Args: item: str. Returns: attribute value."""
        return getattr(self._line, item)

    @property
    def rate(self):
        """Returns float -- override rate for category if present, else baseline rate."""
        return self._overrides.get(self._line.category, self._line.rate)

    @property
    def stressed_outflow(self):
        """Returns float $mm = balance * rate."""
        return self._line.balance * self.rate

    @property
    def stressed_inflow(self):
        """Returns float $mm = balance * rate."""
        return self._line.balance * self.rate


def run_scenario(scenario: Scenario, assets, base_config: OptimizerConfig):
    """Solves one scenario against synthetic bank's outflow/inflow profile.
    Args: scenario: Scenario. assets: list[Asset]. base_config: OptimizerConfig --
      net_cash_outflows overwritten with computed NCO before solving (base_config untouched).
    Returns: dict -- scenario: str, description: str, net_cash_outflows_mm: float,
      nco_detail: dict, result: model.OptimizationResult.
    """
    outflows = [_OverriddenLine(o, scenario.outflow_rate_overrides) for o in build_outflow_profile()]
    inflows = [_OverriddenLine(i, scenario.inflow_rate_overrides) for i in build_inflow_profile()]
    nco_detail = net_cash_outflows(outflows, inflows)
    nco = nco_detail["net_cash_outflows"]

    config = replace(base_config, net_cash_outflows=nco)
    result = solve(assets, config)

    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "net_cash_outflows_mm": nco,
        "nco_detail": nco_detail,
        "result": result,
    }


def run_all_scenarios(assets, base_config: OptimizerConfig, scenarios=None):
    """Runs run_scenario() over list of scenarios.
    Args: assets: list[Asset]. base_config: OptimizerConfig.
      scenarios: list[Scenario]|None, defaults to ALL_SCENARIOS.
    Returns: list[dict], one run_scenario() result per scenario, input order.
    """
    scenarios = scenarios or ALL_SCENARIOS
    return [run_scenario(s, assets, base_config) for s in scenarios]


def summarize_scenarios(scenario_results: list) -> "pandas.DataFrame":
    """Flattens run_all_scenarios() output into display table.
    Args: scenario_results: list[dict].
    Returns: pandas.DataFrame, one row/scenario -- Scenario, Net Cash Outflows ($mm), Status,
      Total HQLA ($mm), LCR (%), Annual Cost ($mm), L1/L2A/L2B ($mm); numeric cols None if not OPTIMAL.
    """
    import pandas as pd
    rows = []
    for sr in scenario_results:
        r = sr["result"]
        rows.append({
            "Scenario": sr["scenario"],
            "Net Cash Outflows ($mm)": round(sr["net_cash_outflows_mm"], 1),
            "Status": r.status,
            "Total HQLA ($mm, adjusted)": round(r.total_hqla_adjusted, 1) if r.status == "OPTIMAL" else None,
            "LCR (%)": round(r.lcr_pct, 1) if r.status == "OPTIMAL" else None,
            "Annual Cost ($mm)": round(r.total_annual_cost_mm, 2) if r.status == "OPTIMAL" else None,
            "L1 ($mm)": round(r.diagnostics.get("l1_adjusted_mm", 0), 1) if r.status == "OPTIMAL" else None,
            "L2A ($mm)": round(r.diagnostics.get("l2a_adjusted_mm", 0), 1) if r.status == "OPTIMAL" else None,
            "L2B ($mm)": round(r.diagnostics.get("l2b_adjusted_mm", 0), 1) if r.status == "OPTIMAL" else None,
        })
    return pd.DataFrame(rows)
