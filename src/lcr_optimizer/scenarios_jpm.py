"""
Stress scenario layer for REAL JPM data

Companion to scenarios.py (which overrides synthetic bank's BCBS238 rates to
absolute values). Doesn't transfer to real data: JPM's rates aren't a
regulatory minimum with a defined "more severe" alternative -- they're
realized, counterparty-blended outcomes. So this module applies a
*multiplier* to JPM's own rate (clamped [0,1]) instead of substituting an
absolute figure -- "Idiosyncratic Shock" means "JPM's real retail run-off
roughly doubles," not a new BCBS-style constant.

TAXONOMY: three non-baseline tiers (Idiosyncratic, Market-Wide, Severely
Adverse = both combined) map to BCBS238's own framing of the LCR's 30-day
stress as "a combination of an idiosyncratic and a market-wide shock"
(para 20-21) -- categories are standard, multiplier values are this
project's own calibration.

CALIBRATION: sized severe-but-survivable for a bank JPM's size (like a real
supervisory severely-adverse scenario), not designed to fail by
construction. Genuine infeasibility still reachable via dashboard's
concentration cap.

Category-key matching: rates.py keys follow "jpm_<q><yy>_<suffix>".
_canonical_suffix() strips the quarter prefix so one scenario definition
applies to any quarter.
"""

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .data import net_cash_outflows
from .data_jpm import JPM_PERIODS
from .model import OptimizerConfig, solve

if TYPE_CHECKING:
    import pandas

_CATEGORY_SUFFIX_RE = re.compile(r"^jpm_[0-9]q[0-9]{2}_(.+)$")


def _canonical_suffix(category: str) -> str:
    """Strips "jpm_<q><yy>_" prefix off category key so multiplier dict
    applies to any quarter.
    Args: category: str, e.g. "jpm_3q25_stable_retail_deposits".
    Returns: str, e.g. "stable_retail_deposits". Raises: ValueError if malformed.
    """
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
    """One named stress scenario, real JPM data.
    name: str, label. description: str. outflow_multipliers: dict[str,float],
    canonical suffix -> multiplier on JPM's real rate, clamped [0,1], default {} (baseline).
    inflow_multipliers: dict[str,float], same shape.
    """
    name: str
    description: str
    outflow_multipliers: dict = field(default_factory=dict)
    inflow_multipliers: dict = field(default_factory=dict)


JPM_BASELINE = JPMScenario(
    name="Baseline",
    description="JPM's own actual disclosed rates for the quarter, no additional stress.",
)

JPM_IDIOSYNCRATIC = JPMScenario(
    name="Idiosyncratic Shock",
    description=(
        "BCBS238's idiosyncratic-shock component: a depositor-confidence run specific "
        "to this bank, on top of JPM's own realized retail rates -- stable and other "
        "retail run-off roughly doubles, brokered deposit run-off increases 50%, each "
        "clamped at 100%."
    ),
    outflow_multipliers={
        "stable_retail_deposits": 2.0,
        "other_retail_funding": 2.0,
        "brokered_deposits": 1.5,
    },
)

JPM_MARKET_WIDE = JPMScenario(
    name="Market-Wide Shock",
    description=(
        "BCBS238's market-wide-shock component: interbank/repo markets tighten on top "
        "of JPM's own realized wholesale rates -- non-operational wholesale funding, "
        "secured funding, and facility drawdown run-off all increase modestly, while "
        "wholesale inflows become somewhat less reliable. Calibrated to be a genuine, "
        "meaningful stress that still leaves this G-SIB LCR-compliant, matching how a "
        "real supervisory severity tier is sized (see module docstring)."
    ),
    outflow_multipliers={
        "nonoperational_wholesale_funding": 1.05,
        "secured_wholesale_funding": 1.05,
        "credit_liquidity_facilities": 1.06,
    },
    inflow_multipliers={
        "secured_lending_inflow": 0.93,
        "unsecured_wholesale_inflow": 0.90,
    },
)

JPM_SEVERELY_ADVERSE = JPMScenario(
    name="Severely Adverse",
    description=(
        "BCBS238's full combined idiosyncratic + market-wide shock -- the scenario the "
        "LCR is actually designed to test resilience against. Both components apply "
        "simultaneously at a somewhat reduced individual severity (a true combined "
        "event compounds two shocks at once rather than simply stacking each one's "
        "full independent severity), still calibrated to stay severe-but-survivable "
        "for JPM's real disclosed asset base."
    ),
    outflow_multipliers={
        "stable_retail_deposits": 1.4,
        "other_retail_funding": 1.4,
        "brokered_deposits": 1.15,
        **JPM_MARKET_WIDE.outflow_multipliers,
    },
    inflow_multipliers={**JPM_MARKET_WIDE.inflow_multipliers},
)

ALL_JPM_SCENARIOS = [JPM_BASELINE, JPM_IDIOSYNCRATIC, JPM_MARKET_WIDE, JPM_SEVERELY_ADVERSE]


class _StressedLine:
    """Wraps OutflowLine/InflowLine so `.rate` reflects multiplier on JPM's
    real rate, clamped [0,1]. Rest passthrough via __getattr__."""

    def __init__(self, line, multipliers: dict):
        self._line = line
        self._multipliers = multipliers

    def __getattr__(self, item):
        """Passthrough for non-overridden attributes. Args: item: str. Returns: attribute value."""
        return getattr(self._line, item)

    @property
    def rate(self) -> float:
        """Returns float [0,1] -- real rate * multiplier for canonical suffix (1.0 default)."""
        suffix = _canonical_suffix(self._line.category)
        mult = self._multipliers.get(suffix, 1.0)
        return min(1.0, max(0.0, self._line.rate * mult))

    @property
    def stressed_outflow(self) -> float:
        """Returns float $mm = balance * rate."""
        return self._line.balance * self.rate

    @property
    def stressed_inflow(self) -> float:
        """Returns float $mm = balance * rate."""
        return self._line.balance * self.rate


def run_jpm_scenario(scenario: JPMScenario, period_key: str, assets, base_config: OptimizerConfig):
    """
    Mirrors scenarios.run_scenario()'s shape, but sourced from real JPM data
    for `period_key` (a JPM_PERIODS key) instead of the synthetic bank.
    `assets` expected to be build_hybrid_asset_universe(period_key, ...)
    result -- passed in so caller can build once with live yields, reuse
    across scenarios.

    Args: scenario: JPMScenario. period_key: str, JPM_PERIODS key. assets: list[Asset].
      base_config: OptimizerConfig -- net_cash_outflows overwritten with computed NCO
      (base_config untouched).
    Returns: dict -- scenario: str, description: str, period_key: str,
      net_cash_outflows_mm: float, nco_detail: dict, result: model.OptimizationResult.
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
    """Runs run_jpm_scenario() over list of scenarios, one quarter.
    Args: period_key: str. assets: list[Asset]. base_config: OptimizerConfig.
      scenarios: list[JPMScenario]|None, defaults to ALL_JPM_SCENARIOS.
    Returns: list[dict], one run_jpm_scenario() result per scenario, input order.
    """
    scenarios = scenarios or ALL_JPM_SCENARIOS
    return [run_jpm_scenario(s, period_key, assets, base_config) for s in scenarios]


def summarize_jpm_scenarios(scenario_results: list) -> "pandas.DataFrame":
    """Flattens run_all_jpm_scenarios() output into display table.
    Args: scenario_results: list[dict].
    Returns: pandas.DataFrame, same columns as scenarios.summarize_scenarios() plus
      "Operationally Solvent (incl. contingent liquidity)" (bool|None, set only when
      non-OPTIMAL + contingent_liquidity_mm > 0; see model.py diagnostic).
    """
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
