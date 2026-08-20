# LCR-Constrained Liquidity Optimizer

Minimum-cost HQLA portfolio optimizer for Basel III's Liquidity Coverage
Ratio, solved as a mixed-integer program with OR-Tools CP-SAT. Runs on
JPMorgan Chase's real public LCR disclosures — four consecutive quarters,
not a synthetic balance sheet. A synthetic v1 (`data.py`/`scenarios.py`)
ships alongside it as a template; the dashboard defaults to real data only.

## Problem

Given a bank's stressed 30-day net cash outflow figure and a universe of
eligible assets (cash, sovereign debt, agency MBS, corporate bonds — each
with a tier, haircut, and yield), pick how much of each asset to hold so:

- Total post-haircut HQLA ≥ net cash outflows (LCR ≥ 100%)
- Level 2 assets (2A + 2B) ≤ 40% of total HQLA
- Level 2B alone ≤ 15% of total HQLA
- Optional: minimum cash floor, single-issuer concentration cap, integer lot sizes

...while minimizing annual opportunity cost (foregone yield vs. a benchmark).
Basel sets the constraints; it doesn't tell you which assets clear them
cheapest. That's the actual decision problem — a genuine MIP once lot sizes
and concentration limits are in, not a toy LP.

## Solver: CP-SAT, not MPSolver

Lot sizes and concentration caps are integer/combinatorial by nature —
bonds trade in lots, not fractional dollars. A classic MIP (CBC/SCIP) would
need a big-M formulation for "if held, hold ≥ N lots" logic, which is
numerically fragile: a bad M either cuts off feasible solutions or wrecks
the LP bound. CP-SAT's reified constraints (`OnlyEnforceIf`) sidestep big-M
entirely, and its search closes these coupled-cap problems faster once
several caps interact (L2 ≤ 40% of a total that includes L2, etc.).

Trade-off: CP-SAT is a pure integer solver — no LP dual values / shadow
prices. For "marginal cost of the LCR requirement going up $1mm," that's a
different question a MIP can't answer, so `model_lp.py` runs a separate
continuous relaxation via MPSolver's GLOP backend specifically for that.
Full reasoning in each module's docstring.

## Real data

JPM's public LCR disclosure (filed quarterly, 12 CFR 249 Subpart R) reports
real dollar figures for HQLA (cash / L1 securities / L2A securities) and
outflow/inflow categories, plus the implied blended rate per category. It
does not report instrument-level detail — no bank discloses which bonds
make up its $678bn of L1 securities. So the asset side is hybrid: real
dollar totals per tier, split across a small illustrative instrument set so
the optimizer has something to choose between and the concentration cap has
teeth. `data_jpm.py::build_hybrid_asset_universe()` documents exactly what's
real vs. illustrative, instrument by instrument.

Four quarters extracted: 2Q25, 3Q25, 4Q25, 1Q26. Every outflow/inflow line
and HQLA figure is checked in `test_data_jpm.py` against JPM's own reported
NCO and eligible HQLA. Measured reproduction accuracy, all four quarters:
**within 0.05%** of disclosed figures (a few $mm to a few hundred $mm out
of hundreds of billions — rate-rounding noise, not error).

Known gap: JPM's disclosed LCR includes a "maturity mismatch add-on" (US
LCR rule specific, no BCBS238 equivalent) that isn't modeled here. Computed
LCR reads a few points higher than JPM's actual reported LCR for the same
quarter as a direct, known consequence.

## Stress testing

`scenarios.py` stresses the synthetic bank by swapping BCBS238's baseline
rate for a worse absolute rate — valid because BCBS238 rates are
regulatory minimums with a defined "more severe" alternative.

That substitution doesn't work for real JPM data — its disclosed rates
aren't a regulatory minimum, they're the realized blended outcome across
thousands of counterparties. `scenarios_jpm.py` instead applies a
multiplier to JPM's own observed rate, clamped to [0%, 100%]. Three tiers —
Idiosyncratic Shock, Market-Wide Shock, Severely Adverse — map to BCBS238's
own framing of the LCR's mandated stress as "a combination of an
idiosyncratic and a market-wide shock" (para 20-21); the multiplier values
are this project's calibration, the categories are the standard taxonomy.

All three tiers are calibrated severe-but-survivable — every quarter ×
scenario combination (16 total) solves **OPTIMAL**, matching how a real
supervisory severely-adverse scenario is sized (barely-survivable for a
G-SIB, not designed to fail by construction). Genuine infeasibility is
still fully reachable — e.g. a 25% single-issuer concentration cap in the
dashboard's Advanced Settings, which real JPM data can't clear (cash +
Treasuries alone dominate real HQLA). When that happens, the optimizer's
infeasibility diagnostic (`OptimizerConfig.contingent_liquidity_mm`) checks
whether JPM's disclosed non-LCR-eligible resources (~$550bn excess
unencumbered securities, ~$420-450bn FHLB/discount-window capacity) would
cover the shortfall — distinguishing "not LCR-compliant" from "operationally
insolvent." The LCR result itself never flips to OPTIMAL because of it.

## Capital adequacy: separate, on purpose

`capital.py` reports JPM's real Basel III Pillar 3 capital position (CET1,
Tier 1, Total capital, SLR) for 4Q25 and 1Q26 — the only two quarters with a
full Pillar 3 filing on hand. It does not feed the optimizer: LCR and
capital adequacy are different Basel pillars with different denominators
(net cash outflows vs. RWA), and the HQLA portfolio sized here is a rounding
error against JPM's ~$2T RWA — nowhere near enough to move a capital ratio.
An SLR/RWA constraint would never bind, so this is a reporting panel, not a
decorative one. 2Q25/3Q25 have no capital section — no Pillar 3 filing for
those quarters, no extrapolation.

## Layout

```
src/lcr_optimizer/
  data.py            Synthetic ~$50bn regional bank + 16-asset HQLA universe (v1 template)
  rates.py            BCBS238 constants (cited by paragraph) + real JPM implied rates per quarter
  data_jpm.py          Real JPM data: 4 quarters of outflows/inflows/HQLA, hybrid asset universe
  market_data.py       Live Treasury-yield fetch, falls back to a fixed snapshot offline
  model.py             Core CP-SAT optimizer + contingent-liquidity infeasibility diagnostic
  model_lp.py           LP relaxation (GLOP) for shadow prices / marginal cost of the LCR requirement
  scenarios.py          Stress scenarios, synthetic bank (absolute BCBS-style overrides)
  scenarios_jpm.py       Stress scenarios, real JPM data (multipliers on JPM's own rates)
  capital.py            Real Basel III Pillar 3 capital ratios (4Q25, 1Q26) — reporting only
dashboard/
  app.py               Streamlit dashboard, real JPM data only, quarter + scenario picker
tests/
  test_model.py          Hand-computed 2-asset case, cap/infeasibility/concentration checks
  test_data_jpm.py         Real-data reproduction checks across all 4 quarters
  test_scenarios_jpm.py     Monotonicity + infeasibility + contingent-liquidity checks
  test_model_lp.py          Hand-derived shadow-price check, LP-vs-MIP bound, real-data smoke tests
  test_capital.py           Capital ratio reproduction + internal consistency checks
  test_market_data.py       Treasury feed parsing, fallback, error-path coverage
  test_dashboard.py         Streamlit AppTest wiring checks (end-to-end sidebar walkthrough)
  test_scenarios.py         Synthetic-bank scenario monotonicity + smoke tests
```

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # source .venv/bin/activate on macOS/Linux
pip install -e .
pip install -r requirements.txt
```

## Tests

```bash
pytest tests/ -v
```

81 tests, 95% line coverage (CI gate: 90%). Start with
`test_hand_computed_two_asset` and `test_lp_relaxation_dual_value_matches_hand_derivation`
— both solve a 2-asset problem by hand (exact numbers, including the LP
shadow price) and confirm the solver matches before trusting either
formulation on anything bigger.

## Dashboard

```bash
streamlit run dashboard/app.py
```

Pick a reporting quarter and stress scenario in the sidebar. Shows optimal
allocation, LCR, marginal cost of the NCO requirement (LP relaxation), tier
cap utilization, a scenario comparison across all four quarters, a
capital-adequacy panel (where available), and a validation table against
JPM's own disclosed figures.

## Performance

CP-SAT solves the 12-instrument real-data problem in ~18ms average. All 16
quarter × scenario combinations solve in ~270ms combined. Never the
bottleneck — the point was getting the formulation and data right, not
solver speed on a problem this small.

## Simplifying assumptions

1. **Composition caps as direct linear constraints.** BCBS238 defines them
   via a sequential "Adjusted Amount" algorithm (Annex 1) to resolve a
   circular definition without an optimizer in the loop. Here the
   circularity is imposed directly as a linear constraint on the decision
   variables — equivalent at the constraint boundary, cleaner with a solver.
2. **Cost = opportunity cost vs. a single benchmark yield**, not a full
   funding/capital-cost model. RWA and leverage-ratio effects on the cost
   of holding HQLA aren't modeled (see capital adequacy above).
3. **Real JPM HQLA instruments have no disclosed yield** — fair value is
   reported, not yield — so every hybrid-universe instrument's `yield_pct`
   is 0.0. Optimizer and LP shadow price are still mathematically correct
   on this input, just less informative than with real per-instrument yield
   data, which doesn't exist publicly.
4. **Maturity mismatch add-on isn't modeled** — computed LCR reads a few
   points higher than JPM's actual reported figure, as a direct consequence.
5. **Real JPM rates are blended, not structural.** "Non-operational
   wholesale funding" at 60% blends fully-insured operational deposits at
   5% through hedge fund balances at 100% — a realized quarterly weighted
   average, not a single BCBS paragraph's rate.
6. **Stress multipliers in `scenarios_jpm.py` are illustrative severity
   increments**, calibrated to be meaningfully worse than baseline while
   staying survivable — not derived from any historical stress event's
   actual realized rates.
