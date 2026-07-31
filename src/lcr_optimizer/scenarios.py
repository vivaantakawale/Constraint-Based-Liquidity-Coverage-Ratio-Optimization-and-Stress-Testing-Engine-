"""
Stress scenario layer.

IMPORTANT CAVEAT (read this before citing scenario numbers as "Basel"):
the BCBS238 run-off/inflow rates already ARE a stress calibration -- the
whole point of the LCR is that the denominator represents a 30-day acute
stress, not business-as-usual. Basel does not publish a second, more severe
"scenario B" set of rates the way this module does. The scenarios below are
a modeling extension on top of the regulatory baseline, meant to answer "how
does the optimal portfolio and its cost shift if the stress is *worse* than
the regulatory minimum calibration assumes" -- which is a reasonable and
common thing for a bank's own internal liquidity stress testing (ILST) to
do, but it is our own severity layer, not a BCBS-specified one. Say this
explicitly if asked in an interview.

Tier haircuts (15%/25%/50%) are NOT shocked here, since those are fixed
regulatory constants applied regardless of scenario -- only outflow/inflow
run-off rates are overridden, which is the mechanism Basel itself uses to
represent stress.
"""

from dataclasses import dataclass, field, replace

from .data import build_outflow_profile, build_inflow_profile, net_cash_outflows
from .model import OptimizerConfig, solve


@dataclass
class Scenario:
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
    """Wraps an OutflowLine/InflowLine so `.rate` reflects a scenario override
    while everything else (name, balance) passes through unchanged."""
    def __init__(self, line, overrides):
        self._line = line
        self._overrides = overrides

    def __getattr__(self, item):
        return getattr(self._line, item)

    @property
    def rate(self):
        return self._overrides.get(self._line.category, self._line.rate)

    @property
    def stressed_outflow(self):
        return self._line.balance * self.rate

    @property
    def stressed_inflow(self):
        return self._line.balance * self.rate


def run_scenario(scenario: Scenario, assets, base_config: OptimizerConfig):
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
    scenarios = scenarios or ALL_SCENARIOS
    return [run_scenario(s, assets, base_config) for s in scenarios]


def summarize_scenarios(scenario_results: list) -> "pandas.DataFrame":
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
