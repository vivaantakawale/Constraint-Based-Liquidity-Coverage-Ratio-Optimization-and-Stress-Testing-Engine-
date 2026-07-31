"""
Stress scenario layer for REAL JPM data.

Companion to scenarios.py, which stresses the synthetic bank's BCBS238-cited
*absolute* rates (e.g. override "retail_stable" from Basel's 5% minimum to
an illustrative 10%). That substitution approach doesn't transfer to real
data: rates.JPM_<q>_OUTFLOW_RATES aren't a regulatory minimum with a
well-defined "more severe" alternative -- they're JPM's own realized,
counterparty-blended outcome for the quarter. There's no independent public
number to substitute in as "JPM's stressed non-operational wholesale rate."

So this module stresses real data the way an internal liquidity stress test
actually would: by applying a *multiplier* to JPM's own observed rate
(clamped to the [0%, 100%] a rate can validly take), not by inventing a
replacement absolute figure. "Deposit Run" here means "JPM's own realized
retail run-off roughly doubles," not "retail run-off becomes some new
BCBS-style constant" -- that framing is honest about what a multiplier on a
real blended rate can and can't claim to represent.

Category-key matching: rates.py's real JPM keys all follow
"jpm_<q><yy>_<canonical_suffix>" (e.g. "jpm_3q25_stable_retail_deposits").
_canonical_suffix() strips the quarter-specific prefix so one scenario
definition (keyed on "stable_retail_deposits") applies unmodified to
whichever quarter is selected -- that's the reason the 4Q25 category keys
were renamed to include a "_4q25_" infix (they originally didn't, which
would have silently broken this matching for that one quarter only).
"""

import re
from dataclasses import dataclass, field, replace

from .data import net_cash_outflows
from .data_jpm import JPM_PERIODS
from .model import OptimizerConfig, solve

_CATEGORY_SUFFIX_RE = re.compile(r"^jpm_[0-9]q[0-9]{2}_(.+)$")


def _canonical_suffix(category: str) -> str:
    m = _CATEGORY_SUFFIX_RE.match(category)
    if not m:
        raise ValueError(
            f"category {category!r} doesn't match the expected "
            f"'jpm_<q><yy>_<suffix>' pattern -- can't apply a quarter-agnostic "
            f"scenario multiplier to it."
        )
    return m.group(1)


@dataclass
class JPMScenario:
    name: str
    description: str
    outflow_multipliers: dict = field(default_factory=dict)  # canonical suffix -> multiplier
    inflow_multipliers: dict = field(default_factory=dict)   # canonical suffix -> multiplier


JPM_BASELINE = JPMScenario(
    name="Baseline",
    description="JPM's own actual disclosed rates for the quarter, no additional stress.",
)

JPM_DEPOSIT_RUN = JPMScenario(
    name="Retail Deposit Run",
    description=(
        "A depositor-confidence shock on top of JPM's own realized retail rates: "
        "stable and other retail run-off roughly doubles, brokered deposit run-off "
        "increases 50%, each clamped at 100%."
    ),
    outflow_multipliers={
        "stable_retail_deposits": 2.0,
        "other_retail_funding": 2.0,
        "brokered_deposits": 1.5,
    },
)

JPM_WHOLESALE_FREEZE = JPMScenario(
    name="Wholesale Funding Freeze",
    description=(
        "Interbank/repo markets seize up on top of JPM's own realized wholesale rates: "
        "non-operational wholesale funding, secured funding, and facility drawdown "
        "run-off all increase, while wholesale inflows become less reliable."
    ),
    outflow_multipliers={
        "nonoperational_wholesale_funding": 1.4,
        "secured_wholesale_funding": 2.0,
        "credit_liquidity_facilities": 1.5,
    },
    inflow_multipliers={
        "secured_lending_inflow": 0.6,
        "unsecured_wholesale_inflow": 0.5,
    },
)

JPM_COMBINED_2008 = JPMScenario(
    name="Combined 2008-Style Shock",
    description=(
        "Deposit run + wholesale freeze simultaneously, with an extra severity "
        "increment on the two categories that drove the 2007-09 crisis hardest: "
        "retail flight and non-financial wholesale withdrawal."
    ),
    outflow_multipliers={
        **JPM_DEPOSIT_RUN.outflow_multipliers,
        **JPM_WHOLESALE_FREEZE.outflow_multipliers,
        "other_retail_funding": 2.5,           # extra increment vs. JPM_DEPOSIT_RUN's 2.0
        "nonoperational_wholesale_funding": 1.6,  # extra increment vs. JPM_WHOLESALE_FREEZE's 1.4
    },
    inflow_multipliers={**JPM_WHOLESALE_FREEZE.inflow_multipliers},
)

ALL_JPM_SCENARIOS = [JPM_BASELINE, JPM_DEPOSIT_RUN, JPM_WHOLESALE_FREEZE, JPM_COMBINED_2008]


class _StressedLine:
    """Wraps an OutflowLine/InflowLine so `.rate` reflects a scenario
    multiplier on JPM's own real disclosed rate, clamped to [0, 1] (a rate
    can't go negative or exceed 100% regardless of how severe the
    multiplier is). Everything else (name, category, balance) passes
    through unchanged via __getattr__."""

    def __init__(self, line, multipliers: dict):
        self._line = line
        self._multipliers = multipliers

    def __getattr__(self, item):
        return getattr(self._line, item)

    @property
    def rate(self) -> float:
        suffix = _canonical_suffix(self._line.category)
        mult = self._multipliers.get(suffix, 1.0)
        return min(1.0, max(0.0, self._line.rate * mult))

    @property
    def stressed_outflow(self) -> float:
        return self._line.balance * self.rate

    @property
    def stressed_inflow(self) -> float:
        return self._line.balance * self.rate


def run_jpm_scenario(scenario: JPMScenario, period_key: str, assets, base_config: OptimizerConfig):
    """
    Mirrors scenarios.run_scenario()'s shape, but sourced from real JPM data
    for `period_key` (a JPM_PERIODS key) instead of the synthetic bank.
    `assets` is expected to be a build_hybrid_asset_universe(period_key, ...)
    result -- passed in rather than rebuilt here so a caller (e.g. the
    dashboard) can build it once with live market yields and reuse it
    across every scenario in a comparison run.
    """
    period = JPM_PERIODS[period_key]
    outflows = [_StressedLine(o, scenario.outflow_multipliers) for o in period["build_outflows"]()]
    inflows = [_StressedLine(i, scenario.inflow_multipliers) for i in period["build_inflows"]()]
    nco_detail = net_cash_outflows(outflows, inflows)
    nco = nco_detail["net_cash_outflows"]

    config = replace(base_config, net_cash_outflows=nco)
    result = solve(assets, config)

    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "period_key": period_key,
        "net_cash_outflows_mm": nco,
        "nco_detail": nco_detail,
        "result": result,
    }


def run_all_jpm_scenarios(period_key: str, assets, base_config: OptimizerConfig, scenarios=None):
    scenarios = scenarios or ALL_JPM_SCENARIOS
    return [run_jpm_scenario(s, period_key, assets, base_config) for s in scenarios]


def summarize_jpm_scenarios(scenario_results: list) -> "pandas.DataFrame":
    import pandas as pd
    rows = []
    for sr in scenario_results:
        r = sr["result"]
        # Only present at all when the caller set OptimizerConfig.contingent_liquidity_mm > 0
        # AND this scenario went INFEASIBLE -- see model.py's _diagnose_infeasibility().
        contingent_sufficient = r.diagnostics.get("contingent_liquidity_sufficient")
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
            "Operationally Solvent (incl. contingent liquidity)": (
                None if r.status == "OPTIMAL" else contingent_sufficient
            ),
        })
    return pd.DataFrame(rows)
