"""
Core LCR optimizer: minimum-cost HQLA portfolio subject to Basel III LCR >= 100%.

SOLVER CHOICE: OR-Tools CP-SAT, not the MPSolver LP/MIP interface.
------------------------------------------------------------------
Why CP-SAT over a classic MIP solver (CBC/SCIP via MPSolver):

1. The realism constraints the brief asks for -- minimum lot sizes,
   single-issuer concentration limits, a cash floor -- are naturally
   integer/combinatorial (lot counts), not continuous. CP-SAT is built
   for exactly this: integer decision variables with linear constraints
   over them, plus first-class reified/indicator constraints
   (`OnlyEnforceIf`) if we later want "if you hold this bond at all, hold
   at least N lots of it" logic -- that needs a big-M formulation in a
   pure LP/MIP model, which is numerically fragile (a badly-chosen M
   either cuts off feasible solutions or wrecks the LP relaxation's
   bound). CP-SAT's reified constraints avoid big-M entirely.
2. CP-SAT's lazy-clause/CP-based search tends to close combinatorial
   MIPs (cardinality limits, this-or-that issuer choices) faster than a
   branch-and-bound LP relaxation does, especially once the model has
   several coupled cap constraints like ours (L2 <= 40% of a total that
   includes L2, etc.).
3. Trade-off, stated up front: CP-SAT is a pure integer solver, so every
   coefficient here is scaled to an integer (see SCALE below), and CP-SAT
   does not give you LP dual values / shadow prices the way MPSolver's
   GLOP would. If a future ask is "what's the marginal cost of the LCR
   requirement" (a shadow-price question), that's a reason to add a
   continuous LP-relaxation solve via MPSolver alongside this model --
   not implemented in v1 since it wasn't asked for.

SCALING: CP-SAT requires integer coefficients in every linear expression.
Haircuts, run-off rates, yields, and caps are all real-valued (e.g. 15%,
4.2%), so every rate is scaled by SCALE and rounded to the nearest integer
before being used in a constraint. SCALE = 1,000,000 gives sub-basis-point
precision, far finer than any of these regulatory rates are specified to,
so the rounding error is immaterial. Composition caps (40%, 15%, single-
issuer limits) are instead cleared with an exact integer cross-multiplication
(e.g. "L2 <= 40% of total" becomes "60*L2 <= 40*L1") wherever the cap is a
clean fraction -- that avoids compounding rounding error on top of the
haircut scaling.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from ortools.sat.python import cp_model

from .data import Asset

SCALE = 1_000_000


@dataclass
class OptimizerConfig:
    net_cash_outflows: float          # $mm, required NCO to cover (LCR denominator)
    cash_floor: float = 0.0            # $mm, minimum Level-1 cash/CB-reserves holding
    issuer_concentration_cap: Optional[float] = None  # e.g. 0.25 => no issuer > 25% of total HQLA
    benchmark_yield: float = 0.085     # annualized, used for opportunity-cost objective
    time_limit_seconds: float = 10.0
    # $mm of non-LCR-eligible fallback liquidity (e.g. FHLB/discount-window
    # borrowing capacity, excess unencumbered securities) a bank could draw
    # on in a real stress but that does NOT count toward the LCR numerator.
    # Used ONLY by _diagnose_infeasibility() to answer a distinct question
    # ("is the bank operationally out of liquidity, or just out of
    # *LCR-eligible* liquidity") -- it never enters build_model()/solve()'s
    # actual LCR constraint, so it can never turn a genuine regulatory
    # INFEASIBLE into a false OPTIMAL. Default 0.0 is a no-op (existing
    # callers that never set this get byte-identical diagnostics to before).
    contingent_liquidity_mm: float = 0.0


@dataclass
class AssetResult:
    name: str
    tier: str
    issuer: str
    lots: int
    amount_mm: float
    adjusted_mm: float          # post-haircut, HQLA-eligible value
    annual_cost_mm: float        # opportunity cost contribution


@dataclass
class OptimizationResult:
    status: str                  # "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"
    allocations: list = field(default_factory=list)   # list[AssetResult]
    total_hqla_adjusted: float = 0.0
    lcr_pct: float = 0.0
    total_annual_cost_mm: float = 0.0
    diagnostics: dict = field(default_factory=dict)


def _scaled(x: float, scale: int = SCALE) -> int:
    return round(x * scale)


def build_model(assets: list[Asset], config: OptimizerConfig):
    """Builds the CP-SAT model. Returns (model, vars_dict) for solving/inspection."""
    model = cp_model.CpModel()

    lot_vars = {}
    amount_vars = {}   # amount_mm = lot_count * min_lot  (both integer, $mm)
    adjusted_vars = {}  # amount_mm * (1 - haircut), scaled by SCALE

    for a in assets:
        max_available = a.available if a.available is not None else float("inf")
        if max_available == float("inf"):
            max_lots = 10_000  # effectively unlimited for cash; keeps the domain finite for CP-SAT
        else:
            max_lots = int(max_available // a.min_lot)

        lot = model.NewIntVar(0, max_lots, f"lots_{a.name}")
        lot_vars[a.name] = lot

        amount = model.NewIntVar(0, max_lots * int(a.min_lot), f"amount_{a.name}")
        model.Add(amount == lot * int(a.min_lot))
        amount_vars[a.name] = amount

        if a.is_hqla:
            factor = _scaled(1.0 - a.haircut)  # e.g. haircut 0.15 -> 850,000
        else:
            factor = 0  # non-HQLA assets never count toward the LCR numerator
        adjusted = model.NewIntVar(0, max_lots * int(a.min_lot) * SCALE, f"adj_{a.name}")
        model.Add(adjusted == amount * factor)
        adjusted_vars[a.name] = adjusted

    # --- Tier aggregates ---
    def tier_sum(tier_name):
        return sum(adjusted_vars[a.name] for a in assets if a.tier == tier_name)

    l1_adj = tier_sum("L1")
    l2a_adj = tier_sum("L2A")
    l2b_adj = (tier_sum("L2B_RMBS") + tier_sum("L2B_CORP") + tier_sum("L2B_EQUITY"))
    total_hqla_adj = l1_adj + l2a_adj + l2b_adj

    # --- Constraint 1: LCR >= 100%  <=>  Total HQLA (adjusted) >= Net Cash Outflows ---
    nco_scaled = _scaled(config.net_cash_outflows)
    model.Add(total_hqla_adj >= nco_scaled)

    # --- Constraint 2: Level 2 (2A+2B) <= 40% of total HQLA ---
    # L2 <= 0.40*(L1+L2+L2B)  <=>  0.60*L2 <= 0.40*L1  <=>  60*L2 <= 40*L1  (exact, no extra rounding)
    model.Add(60 * (l2a_adj + l2b_adj) <= 40 * l1_adj)

    # --- Constraint 3: Level 2B <= 15% of total HQLA ---
    # L2B <= 0.15*(L1+L2A+L2B)  <=>  0.85*L2B <= 0.15*(L1+L2A)  <=>  85*L2B <= 15*(L1+L2A)
    model.Add(85 * l2b_adj <= 15 * (l1_adj + l2a_adj))

    # --- Constraint 4: minimum cash floor (Level 1 cash specifically) ---
    cash_assets = [a for a in assets if a.tier == "L1" and "Cash" in a.name]
    if config.cash_floor > 0 and cash_assets:
        model.Add(sum(amount_vars[a.name] for a in cash_assets) >= int(config.cash_floor))

    # --- Constraint 5 (optional): single-issuer concentration limit ---
    if config.issuer_concentration_cap:
        frac = Fraction(config.issuer_concentration_cap).limit_denominator(1000)
        issuers = sorted(set(a.issuer for a in assets if a.is_hqla))
        for issuer in issuers:
            issuer_adj = sum(adjusted_vars[a.name] for a in assets if a.issuer == issuer and a.is_hqla)
            # issuer_adj <= cap * total  <=>  frac.denominator*issuer_adj <= frac.numerator*total
            model.Add(frac.denominator * issuer_adj <= frac.numerator * total_hqla_adj)

    # --- Objective: minimize total annual opportunity cost ---
    # cost_i = (benchmark_yield - yield_i), scaled; can be negative if yield_i > benchmark
    # (i.e. the asset is *cheaper than free* to hold -- the solver will happily max it
    # out up to its `available` bound, which is why every asset must have a finite bound).
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
    When the primary problem is infeasible, solve a secondary problem:
    "ignoring the NCO requirement, what's the MOST adjusted HQLA achievable
    given available supply and the composition caps?" This turns a bare
    INFEASIBLE into an actionable diagnostic instead of a silent failure.
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
    # Maximize() call above overwrites the Minimize() set inside build_model;
    # CP-SAT only keeps the last objective set, which is what we want here.
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
        # Secondary, clearly-separate question: is the bank operationally out
        # of liquidity, or just out of *LCR-eligible* liquidity? This never
        # touches the regulatory result above -- LCR stays INFEASIBLE either
        # way, since contingent_liquidity_mm sources are excluded from
        # eligible HQLA by definition (that's why the bank's own disclosure
        # reports them separately). This only tells you whether the bank
        # would survive the stress operationally, not whether it's compliant.
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
