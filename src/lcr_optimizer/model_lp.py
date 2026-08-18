"""
LP relaxation of LCR optimizer for shadow price / marginal cost analysis

model.py's CP SAT is right for allocation decision but is a pure integer solver, no LP dual values
This module: same objective/constraints, lot counts relaxed to continuous $mm, solved via MPSolver GLOP 
(true simplex, has defined duals)

HEADLINE NUMBER: dual value on LCR constraint = marginal annual opportunity cost, $mm/yr per $1mm NCO d(cost)/d(NCO)

CAVEATS:
1. LP optimal cost is a lower bound on MIP's (relaxing integrality can only help) 
    LP cost <= MIP cost, real gap not noise
2. "MIP shadow price" isn't well defined (duality is an LP/convex-program result) 
    complementary analysis, not a precise version of one MIP number
3. No SCALE-style integer scaling needed 
    GLOP is native floating point
"""

from dataclasses import dataclass, field
from typing import Optional

from ortools.linear_solver import pywraplp

from .data import Asset
from .model import OptimizerConfig


@dataclass
class LPAssetResult:
    """One asset's allocation, LPRelaxationResult
    Mirrors model.AssetResult, no `lots` (continuous)
    name/tier/issuer: str
    amount_mm: float, continuous $mm, not lot-rounded
    adjusted_mm: float, post-haircut $mm
    annual_cost_mm: float $mm/yr
    """
    name: str
    tier: str
    issuer: str
    amount_mm: float
    adjusted_mm: float
    annual_cost_mm: float


@dataclass
class LPRelaxationResult:
    """Return value of solve_lp_relaxation()
    status: str, "OPTIMAL"/"FEASIBLE"/"INFEASIBLE"/"UNBOUNDED"/"UNKNOWN"/"SOLVER_UNAVAILABLE"
    allocations: list[LPAssetResult], empty unless OPTIMAL/FEASIBLE
    total_hqla_adjusted: float $mm
    lcr_pct: float
    total_annual_cost_mm: float $mm/yr
    All 0.0 if not solved
    lcr_marginal_cost_mm_per_mm: float|None headline number, dual on LCR constraint, $mm/yr per $1mm NCO; None unless status exactly "OPTIMAL"
    diagnostics: dict l1/l2a/l2b_adjusted_mm, float $mm
    """
    status: str
    allocations: list = field(default_factory=list)
    total_hqla_adjusted: float = 0.0
    lcr_pct: float = 0.0
    total_annual_cost_mm: float = 0.0
    lcr_marginal_cost_mm_per_mm: Optional[float] = None
    diagnostics: dict = field(default_factory=dict)


_L2B_TIERS = ("L2B_RMBS", "L2B_CORP", "L2B_EQUITY")


def solve_lp_relaxation(assets: list[Asset], config: OptimizerConfig) -> LPRelaxationResult:
    """Builds + solves continuous LP relaxation via MPSolver GLOP for LCR constraint's dual value (marginal cost)
    See module docstring for why this needs a separate solve

    ARGS: assets: list[Asset], same shape as model.solve(). config: OptimizerConfig, same shape
        note config.time_limit_seconds is ignored (GLOP has no limit set here)
    RETURNS: LPRelaxationResult
    """
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        return LPRelaxationResult(status="SOLVER_UNAVAILABLE")

    amount_vars = {}
    for a in assets:
        if a.available is not None and a.available != float("inf"):
            ub = a.available
        else:
            # Mirrors model.py's build_model(): "unbounded" is capped at 10,000 lots for a finite domain, not actually unlimited

            ub = 10_000 * a.min_lot
        amount_vars[a.name] = solver.NumVar(0.0, float(ub), f"amount_{a.name}")

    def adjusted_expr(a: Asset):
        """Post haircut LP expression for one asset
        ARGS: a: Asset
        RETURNS: LP expression, 0.0 for non HQLA
        """
        if not a.is_hqla:
            return 0.0
        return amount_vars[a.name] * (1.0 - a.haircut)

    l1_assets = [a for a in assets if a.tier == "L1"]
    l2a_assets = [a for a in assets if a.tier == "L2A"]
    l2b_assets = [a for a in assets if a.tier in _L2B_TIERS]

    l1_adj = sum((adjusted_expr(a) for a in l1_assets), 0.0)
    l2a_adj = sum((adjusted_expr(a) for a in l2a_assets), 0.0)
    l2b_adj = sum((adjusted_expr(a) for a in l2b_assets), 0.0)
    total_hqla_adj = l1_adj + l2a_adj + l2b_adj

    # Constraint 1: LCR >= 100% <=> total adjusted HQLA >= NCO 
    # This is the constraint whose dual value is headline number
    lcr_constraint = solver.Add(total_hqla_adj >= config.net_cash_outflows, "lcr_constraint")

    # Constraint 2: Level 2 (2A+2B) <= 40% of total HQLA
    solver.Add(0.60 * (l2a_adj + l2b_adj) <= 0.40 * l1_adj)

    # Constraint 3: Level 2B <= 15% of total HQLA 
    solver.Add(0.85 * l2b_adj <= 0.15 * (l1_adj + l2a_adj))

    # Constraint 4: cash floor 
    cash_assets = [a for a in assets if a.tier == "L1" and "Cash" in a.name]
    if config.cash_floor > 0 and cash_assets:
        solver.Add(sum(amount_vars[a.name] for a in cash_assets) >= config.cash_floor)

    # Constraint 5 (optional): single issuer concentration limit 
    if config.issuer_concentration_cap:
        cap = config.issuer_concentration_cap
        issuers = sorted(set(a.issuer for a in assets if a.is_hqla))
        for issuer in issuers:
            issuer_adj = sum((adjusted_expr(a) for a in assets if a.issuer == issuer and a.is_hqla), 0.0)
            solver.Add(issuer_adj <= cap * total_hqla_adj)

    # Objective: minimize total annual opportunity cost 
    cost_terms = [
        (config.benchmark_yield - a.yield_pct) * amount_vars[a.name]
        for a in assets
    ]
    solver.Minimize(sum(cost_terms))

    status = solver.Solve()
    status_name = {
        pywraplp.Solver.OPTIMAL: "OPTIMAL",
        pywraplp.Solver.FEASIBLE: "FEASIBLE",
        pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
        pywraplp.Solver.UNBOUNDED: "UNBOUNDED",
    }.get(status, "UNKNOWN")

    if status_name not in ("OPTIMAL", "FEASIBLE"):
        return LPRelaxationResult(status=status_name)

    allocations = []
    total_cost = 0.0
    for a in assets:
        amount = amount_vars[a.name].solution_value()
        if amount <= 1e-9:
            continue
        adjusted = amount * (1.0 - a.haircut) if a.is_hqla else 0.0
        annual_cost = amount * (config.benchmark_yield - a.yield_pct)
        total_cost += annual_cost
        allocations.append(LPAssetResult(
            name=a.name, tier=a.tier, issuer=a.issuer,
            amount_mm=amount, adjusted_mm=adjusted, annual_cost_mm=annual_cost,
        ))

    total_hqla = sum(r.adjusted_mm for r in allocations)
    lcr_pct = 100.0 * total_hqla / config.net_cash_outflows if config.net_cash_outflows > 0 else float("inf")

    # dual_value() is only meaningful for true optimal LP basis
    lcr_dual = lcr_constraint.dual_value() if status_name == "OPTIMAL" else None

    return LPRelaxationResult(
        status=status_name,
        allocations=sorted(allocations, key=lambda r: -r.adjusted_mm),
        total_hqla_adjusted=total_hqla,
        lcr_pct=lcr_pct,
        total_annual_cost_mm=total_cost,
        lcr_marginal_cost_mm_per_mm=lcr_dual,
        diagnostics={
            "l1_adjusted_mm": sum(r.adjusted_mm for r in allocations if r.tier == "L1"),
            "l2a_adjusted_mm": sum(r.adjusted_mm for r in allocations if r.tier == "L2A"),
            "l2b_adjusted_mm": sum(r.adjusted_mm for r in allocations if r.tier in _L2B_TIERS),
        },
    )
