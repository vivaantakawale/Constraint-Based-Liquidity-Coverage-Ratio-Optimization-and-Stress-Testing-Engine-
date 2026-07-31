"""
LP relaxation of the LCR optimizer, for shadow-price / marginal-cost analysis.

model.py's module docstring flags this explicitly as a deliberate v1 gap:
CP-SAT is the right choice for the actual allocation decision (integer lot
counts, reified concentration/lot-size constraints -- see that docstring
for the full reasoning), but it's a pure integer solver and doesn't expose
LP dual values the way a continuous solve does. This module is that
continuous solve: same objective, same constraints, decision variables
relaxed from integer lot counts to continuous dollar amounts, solved via
OR-Tools' MPSolver GLOP backend (a true simplex LP solver, which *does*
have well-defined dual values).

THE HEADLINE NUMBER this gives you that model.py alone cannot: the dual
value on the LCR constraint (total adjusted HQLA >= NCO) is the marginal
annual opportunity cost, in $mm/yr per $1mm of net cash outflows -- i.e.
d(cost)/d(NCO). That answers a real treasury question the MIP solve can't:
"what does it cost us, per dollar, to have to cover more net cash
outflows" -- useful for pricing the cost of a business decision that grows
the outflow base, or for justifying a request to shrink it.

CAVEATS (say these if asked):
1. The LP relaxation's optimal objective value is a lower bound on the
   MIP's (relaxing integrality can only improve or match the true integer
   optimum), so LP cost <= MIP cost. For large-lot assets relative to the
   portfolio size this gap is usually small, but it is a real
   approximation gap, not numerical noise.
2. "MIP shadow price" isn't a well-defined concept the way an LP dual is
   (duality theory is an LP/convex-program result) -- this is a genuinely
   different, complementary analysis, not a more-precise version of the
   same number the MIP would give you if you asked it nicely.
3. No SCALE-style integer coefficient scaling is needed here (unlike
   model.py) -- GLOP is a native floating-point LP solver, so haircuts,
   rates, and caps enter as exact real coefficients.
"""

from dataclasses import dataclass, field
from typing import Optional

from ortools.linear_solver import pywraplp

from .data import Asset
from .model import OptimizerConfig


@dataclass
class LPAssetResult:
    name: str
    tier: str
    issuer: str
    amount_mm: float       # continuous $mm, NOT rounded to a lot multiple
    adjusted_mm: float      # post-haircut, HQLA-eligible value
    annual_cost_mm: float


@dataclass
class LPRelaxationResult:
    status: str
    allocations: list = field(default_factory=list)   # list[LPAssetResult]
    total_hqla_adjusted: float = 0.0
    lcr_pct: float = 0.0
    total_annual_cost_mm: float = 0.0
    # $mm/yr of additional cost per additional $1mm of NCO -- the headline
    # number. None if the LCR constraint wasn't binding / has no valid dual
    # (e.g. NCO == 0, or the solve wasn't optimal).
    lcr_marginal_cost_mm_per_mm: Optional[float] = None
    diagnostics: dict = field(default_factory=dict)


_L2B_TIERS = ("L2B_RMBS", "L2B_CORP", "L2B_EQUITY")


def solve_lp_relaxation(assets: list[Asset], config: OptimizerConfig) -> LPRelaxationResult:
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        return LPRelaxationResult(status="SOLVER_UNAVAILABLE")

    amount_vars = {}
    for a in assets:
        if a.available is not None and a.available != float("inf"):
            ub = a.available
        else:
            # Mirrors model.py's build_model(): "unbounded" (available=inf,
            # e.g. cash) is capped at 10,000 lots for a finite domain, not
            # actually unlimited. Must match that convention exactly --
            # an inconsistent cap here would make the LP report OPTIMAL on
            # an NCO the MIP correctly reports INFEASIBLE for, which would
            # silently misrepresent what "feasible" means between the two.
            ub = 10_000 * a.min_lot
        amount_vars[a.name] = solver.NumVar(0.0, float(ub), f"amount_{a.name}")

    def adjusted_expr(a: Asset):
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

    # --- Constraint 1: LCR >= 100% <=> total adjusted HQLA >= NCO ---
    # This is the constraint whose dual value is the headline number.
    lcr_constraint = solver.Add(total_hqla_adj >= config.net_cash_outflows, "lcr_constraint")

    # --- Constraint 2: Level 2 (2A+2B) <= 40% of total HQLA ---
    solver.Add(0.60 * (l2a_adj + l2b_adj) <= 0.40 * l1_adj)

    # --- Constraint 3: Level 2B <= 15% of total HQLA ---
    solver.Add(0.85 * l2b_adj <= 0.15 * (l1_adj + l2a_adj))

    # --- Constraint 4: cash floor ---
    cash_assets = [a for a in assets if a.tier == "L1" and "Cash" in a.name]
    if config.cash_floor > 0 and cash_assets:
        solver.Add(sum(amount_vars[a.name] for a in cash_assets) >= config.cash_floor)

    # --- Constraint 5 (optional): single-issuer concentration limit ---
    if config.issuer_concentration_cap:
        cap = config.issuer_concentration_cap
        issuers = sorted(set(a.issuer for a in assets if a.is_hqla))
        for issuer in issuers:
            issuer_adj = sum((adjusted_expr(a) for a in assets if a.issuer == issuer and a.is_hqla), 0.0)
            solver.Add(issuer_adj <= cap * total_hqla_adj)

    # --- Objective: minimize total annual opportunity cost ---
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

    # dual_value() is only meaningful for a true optimal LP basis.
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
