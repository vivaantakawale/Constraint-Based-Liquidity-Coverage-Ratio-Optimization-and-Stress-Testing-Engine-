"""
Core LCR optimizer: minimum cost HQLA portfolio, Basel III LCR >= 100%

SOLVER: OR-Tools CP-SAT, not MPSolver LP/MIP
    Why:
1. Lot sizes, concentration caps, cash floor are integer/combinatorial.
   CP-SAT's reified constraints (OnlyEnforceIf) avoid the big-M formulation
   a classic MIP would need for "if held, hold >= N lots" logic -- big-M is
   numerically fragile (bad M cuts off feasible solutions or wrecks the
   LP bound).
2. CP-SAT's search tends to close coupled-cap MIPs (L2 <= 40% of a total
   that includes L2, etc.) faster than branch-and-bound.
3. Trade-off: pure integer solver, no LP dual values/shadow prices --
   coefficients scaled to integer (SCALE below); see model_lp.py for the
   separate continuous solve that gives shadow prices.

SCALING: CP-SAT needs integer coefficients. Rates/haircuts/yields scaled by
SCALE (1,000,000, sub-basis-point precision) and rounded. Composition caps
cleared via exact integer cross-multiplication ("L2<=40%" -> "60*L2<=40*L1")
to avoid compounding rounding error.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from ortools.sat.python import cp_model

from .data import Asset

SCALE = 1_000_000


@dataclass
class OptimizerConfig:
    """Inputs to build_model()/solve(). Dollar figures in $mm.
    net_cash_outflows: float, required NCO (LCR denominator).
    cash_floor: float, min L1 cash held, default 0.0.
    issuer_concentration_cap: float|None, e.g. 0.25 = no issuer > 25% of HQLA, default None.
    benchmark_yield: float decimal, opportunity-cost baseline, default 0.085.
    time_limit_seconds: float, CP-SAT time budget, default 10.0.
    contingent_liquidity_mm: float, non-LCR-eligible fallback liquidity (FHLB/discount-window,
      excess unencumbered securities). Used only by _diagnose_infeasibility() -- never enters
      the actual LCR constraint. Default 0.0, no-op.
    """
    net_cash_outflows: float
    cash_floor: float = 0.0
    issuer_concentration_cap: Optional[float] = None
    benchmark_yield: float = 0.085
    time_limit_seconds: float = 10.0
    contingent_liquidity_mm: float = 0.0


@dataclass
class AssetResult:
    """One asset's allocation in solved OptimizationResult.
    name/tier/issuer: str, copied from source Asset. lots: int, lots held.
    amount_mm: float, $mm = lots*min_lot. adjusted_mm: float, post-haircut $mm.
    annual_cost_mm: float $mm/yr, can be negative if yield exceeds benchmark.
    """
    name: str
    tier: str
    issuer: str
    lots: int
    amount_mm: float
    adjusted_mm: float
    annual_cost_mm: float


@dataclass
class OptimizationResult:
    """Return value of solve().
    status: str, "OPTIMAL"/"FEASIBLE"/"INFEASIBLE"/"UNKNOWN".
    allocations: list[AssetResult], empty unless OPTIMAL/FEASIBLE, sorted by adjusted_mm desc.
    total_hqla_adjusted: float $mm. lcr_pct: float, 100*total_hqla_adjusted/NCO.
    total_annual_cost_mm: float $mm/yr. All three 0.0 if infeasible.
    diagnostics: dict -- on success: l1/l2a/l2b_adjusted_mm (float $mm), solve_wall_time_s
      (float sec). On infeasibility: see _diagnose_infeasibility() Returns.
    """
    status: str
    allocations: list = field(default_factory=list)
    total_hqla_adjusted: float = 0.0
    lcr_pct: float = 0.0
    total_annual_cost_mm: float = 0.0
    diagnostics: dict = field(default_factory=dict)


def _scaled(x: float, scale: int = SCALE) -> int:
    """Rounds real value to integer at `scale` precision (CP-SAT needs integer coefficients).
    Args: x: float. scale: int, default SCALE (1,000,000). Returns: int.
    """
    return round(x * scale)


def build_model(assets: list[Asset], config: OptimizerConfig):
    """Builds CP-SAT model: integer lot count vars per asset, LCR/cap/floor/concentration constraints, minimize cost objective 
    See module docstring for solver choice/scaling rationale

    ARGS: assets: list[Asset]. config: OptimizerConfig.
    RETURNS: tuple[cp_model.CpModel, dict] (model, ctx)
    ctx: lot_vars, amount_vars, adjusted_vars (dict[str,IntVar] by asset name), 
    total_hqla_adj, l1_adj, l2a_adj, l2b_adj (CP-SAT expressions, SCALE-scaled)
    """
    model = cp_model.CpModel()

    lot_vars = {}
    amount_vars = {}   # amount_mm = lot_count * min_lot  (both integer, $mm)
    adjusted_vars = {}  # amount_mm * (1 - haircut), scaled by SCALE

    for a in assets:
        max_available = a.available if a.available is not None else float("inf")
        if max_available == float("inf"):
            max_lots = 10_000  # effectively unlimited for cash; keeps the domain finite for CP SAT
        else:
            max_lots = int(max_available // a.min_lot)

        lot = model.NewIntVar(0, max_lots, f"lots_{a.name}")
        lot_vars[a.name] = lot

        amount = model.NewIntVar(0, max_lots * int(a.min_lot), f"amount_{a.name}")
        model.Add(amount == lot * int(a.min_lot))
        amount_vars[a.name] = amount

        if a.is_hqla:
            factor = _scaled(1.0 - a.haircut)
        else:
            factor = 0  # non-HQLA assets never count toward LCR numerator
        adjusted = model.NewIntVar(0, max_lots * int(a.min_lot) * SCALE, f"adj_{a.name}")
        model.Add(adjusted == amount * factor)
        adjusted_vars[a.name] = adjusted

    # Tier aggregates 
    def tier_sum(tier_name):
        """Sums adjusted_vars for assets matching tier_name.
        Args: tier_name: str. Returns: CP-SAT expression, SCALE-scaled $mm."""
        return sum(adjusted_vars[a.name] for a in assets if a.tier == tier_name)

    l1_adj = tier_sum("L1")
    l2a_adj = tier_sum("L2A")
    l2b_adj = (tier_sum("L2B_RMBS") + tier_sum("L2B_CORP") + tier_sum("L2B_EQUITY"))
    total_hqla_adj = l1_adj + l2a_adj + l2b_adj

    # Constraint 1: LCR >= 100%  <=>  Total HQLA (adjusted) >= Net Cash Outflows 
    nco_scaled = _scaled(config.net_cash_outflows)
    model.Add(total_hqla_adj >= nco_scaled)

    # Constraint 2: Level 2 (2A+2B) <= 40% of total HQLA
    # L2 <= 0.40*(L1+L2+L2B)  <=>  0.60*L2 <= 0.40*L1  <=>  60*L2 <= 40*L1  (exact, no extra rounding)
    model.Add(60 * (l2a_adj + l2b_adj) <= 40 * l1_adj)

    # Constraint 3: Level 2B <= 15% of total HQLA 
    # L2B <= 0.15*(L1+L2A+L2B)  <=>  0.85*L2B <= 0.15*(L1+L2A)  <=>  85*L2B <= 15*(L1+L2A)
    model.Add(85 * l2b_adj <= 15 * (l1_adj + l2a_adj))

    # Constraint 4: minimum cash floor (Level 1 cash specifically) 
    cash_assets = [a for a in assets if a.tier == "L1" and "Cash" in a.name]
    if config.cash_floor > 0 and cash_assets:
        model.Add(sum(amount_vars[a.name] for a in cash_assets) >= int(config.cash_floor))

    # Constraint 5 (optional): single-issuer concentration limit 
    if config.issuer_concentration_cap:
        frac = Fraction(config.issuer_concentration_cap).limit_denominator(1000)
        issuers = sorted(set(a.issuer for a in assets if a.is_hqla))
        for issuer in issuers:
            issuer_adj = sum(adjusted_vars[a.name] for a in assets if a.issuer == issuer and a.is_hqla)
            # issuer_adj <= cap * total  <=>  frac.denominator*issuer_adj <= frac.numerator*total
            model.Add(frac.denominator * issuer_adj <= frac.numerator * total_hqla_adj)

    # Objective: minimize total annual opportunity cost
    # cost_i = (benchmark_yield - yield_i), scaled; can be negative if yield_i > benchmark

    cost_terms = []
    for a in assets:
        cost_coef = _scaled(config.benchmark_yield - a.yield_pct)
        cost_terms.append(cost_coef * amount_vars[a.name])
    model.Minimize(sum(cost_terms))

    ctx = {
        "lot_vars": lot_vars,
        "amount_vars": amount_vars,
        "adjusted_vars": adjusted_vars,
        "total_hqla_adj": total_hqla_adj,
        "l1_adj": l1_adj,
        "l2a_adj": l2a_adj,
        "l2b_adj": l2b_adj,
    }
    return model, ctx


def _diagnose_infeasibility(assets: list[Asset], config: OptimizerConfig) -> dict:
    """
    On infeasibility solves secondary problem: 
    "ignoring NCO, what's max adjusted HQLA achievable given supply + composition caps?" 
    Turns bare INFEASIBLE into actionable diagnostic

    ARGS: assets: list[Asset], same as solve(). config: OptimizerConfig, same as solve()
        (net_cash_outflows ignored, sub-problem uses 0.0 instead)
    RETURNS: dict
    On success: max_achievable_hqla_mm, required_nco_mm, shortfall_mm (float $mm), message: str
        If contingent_liquidity_mm > 0, adds:
            contingent_liquidity_available_mm, contingent_liquidity_needed_mm (float $mm), contingent_liquidity_sufficient: bool, contingent_liquidity_message: str
        If sub-problem fails: {"message": str} only
    """
    relaxed_config = OptimizerConfig(
        net_cash_outflows=0.0,
        cash_floor=config.cash_floor,
        issuer_concentration_cap=config.issuer_concentration_cap,
        benchmark_yield=config.benchmark_yield,
        time_limit_seconds=config.time_limit_seconds,
    )
    model, ctx = build_model(assets, relaxed_config)
    model.Maximize(ctx["total_hqla_adj"])
    # Maximize() call above overwrites Minimize() set inside build_model;
    # CP-SAT only keeps last objective set, which is what we want
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.time_limit_seconds
    status = solver.Solve(model)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        max_hqla = solver.Value(ctx["total_hqla_adj"]) / SCALE
        shortfall = config.net_cash_outflows - max_hqla
        diagnostics = {
            "max_achievable_hqla_mm": max_hqla,
            "required_nco_mm": config.net_cash_outflows,
            "shortfall_mm": shortfall,
            "message": (
                f"Even using all available eligible assets at maximum tier caps, "
                f"the largest achievable adjusted HQLA is ${max_hqla:,.1f}mm, which is "
                f"${shortfall:,.1f}mm short of the "
                f"${config.net_cash_outflows:,.1f}mm required to hit LCR=100%. "
                f"The asset universe's total supply (or the 40%/15% composition caps) "
                f"cannot support this outflow level -- this is a genuine infeasibility, "
                f"not a solver bug."
            ),
        }

        if config.contingent_liquidity_mm > 0:
            sufficient = config.contingent_liquidity_mm >= shortfall
            diagnostics.update({
                "contingent_liquidity_available_mm": config.contingent_liquidity_mm,
                "contingent_liquidity_needed_mm": max(shortfall, 0.0),
                "contingent_liquidity_sufficient": sufficient,
                "contingent_liquidity_message": (
                    (
                        f"However, drawing on ${config.contingent_liquidity_mm:,.1f}mm of "
                        f"non-LCR-eligible contingent liquidity (e.g. FHLB/discount-window "
                        f"borrowing capacity, excess unencumbered securities) would cover "
                        f"the ${shortfall:,.1f}mm shortfall -- the bank is operationally "
                        f"solvent under this stress even though it is NOT LCR-compliant."
                    ) if sufficient else (
                        f"Even the ${config.contingent_liquidity_mm:,.1f}mm of disclosed "
                        f"non-LCR-eligible contingent liquidity is insufficient to cover the "
                        f"${shortfall:,.1f}mm shortfall -- this stress level exceeds the "
                        f"bank's total disclosed liquidity resources, not just its "
                        f"LCR-eligible ones."
                    )
                ),
            })
        return diagnostics
    return {"message": "Diagnostic sub-problem also failed to solve; check asset universe is non-empty."}


def solve(assets: list[Asset], config: OptimizerConfig) -> OptimizationResult:
    """Builds + solves CP-SAT model for minimum-cost HQLA allocation. Main entry point.
    Args: assets: list[Asset]. config: OptimizerConfig.
    Returns: OptimizationResult. If infeasible: allocations empty, diagnostics from
      _diagnose_infeasibility() instead.
    """
    model, ctx = build_model(assets, config)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        diagnostics = _diagnose_infeasibility(assets, config)
        return OptimizationResult(status=status_name, diagnostics=diagnostics)

    allocations = []
    total_cost = 0.0
    for a in assets:
        lots = solver.Value(ctx["lot_vars"][a.name])
        if lots == 0:
            continue
        amount = solver.Value(ctx["amount_vars"][a.name])
        adjusted = solver.Value(ctx["adjusted_vars"][a.name]) / SCALE
        annual_cost = amount * (config.benchmark_yield - a.yield_pct)
        total_cost += annual_cost
        allocations.append(AssetResult(
            name=a.name, tier=a.tier, issuer=a.issuer,
            lots=lots, amount_mm=amount, adjusted_mm=adjusted,
            annual_cost_mm=annual_cost,
        ))

    total_hqla = solver.Value(ctx["total_hqla_adj"]) / SCALE
    lcr_pct = 100.0 * total_hqla / config.net_cash_outflows if config.net_cash_outflows > 0 else float("inf")

    return OptimizationResult(
        status=status_name,
        allocations=sorted(allocations, key=lambda r: -r.adjusted_mm),
        total_hqla_adjusted=total_hqla,
        lcr_pct=lcr_pct,
        total_annual_cost_mm=total_cost,
        diagnostics={
            "l1_adjusted_mm": solver.Value(ctx["l1_adj"]) / SCALE,
            "l2a_adjusted_mm": solver.Value(ctx["l2a_adj"]) / SCALE,
            "l2b_adjusted_mm": solver.Value(ctx["l2b_adj"]) / SCALE,
            "solve_wall_time_s": solver.WallTime(),
        },
    )
