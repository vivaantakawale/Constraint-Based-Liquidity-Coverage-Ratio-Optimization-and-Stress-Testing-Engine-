"""
LCR Optimizer -- Streamlit dashboard.

Runs on a hybrid dataset: JPMorgan Chase's real, publicly disclosed LCR
numbers (outflow/inflow categories, and total HQLA capacity by tier) with
the instrument-level asset structure from data.py rescaled to match those
real tier totals. Every dollar that JPM actually discloses is real; the
one thing kept synthetic is the split of each tier into specific
instruments (issuer, yield, min lot), since no bank -- JPM included --
publishes that breakdown. See lcr_optimizer/data_jpm.py's
build_hybrid_asset_universe() docstring for the exact split.

Priorities per project brief: speed and clarity over visual polish. This is
an internal analysis tool, not a public-facing product, so it targets one
light-mode Plotly theme rather than full dark-mode parity.

Run with:  streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import plotly.io as pio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lcr_optimizer.capital import JPM_CAPITAL_PERIODS, compute_capital_adequacy
from lcr_optimizer.data_jpm import JPM_PERIODS, build_hybrid_asset_universe
from lcr_optimizer.market_data import fetch_treasury_yields
from lcr_optimizer.model import OptimizerConfig
from lcr_optimizer.model_lp import solve_lp_relaxation
from lcr_optimizer.scenarios_jpm import ALL_JPM_SCENARIOS, run_jpm_scenario, summarize_jpm_scenarios

pio.templates.default = "plotly_white"
# --- Palette (fixed categorical/ordinal slots; see dataviz skill guidance) ---
TIER_COLOR = {
    "L1": "#1c5cab",         # ordinal blue ramp, darkest = highest quality tier
    "L2A": "#3987e5",
    "L2B_RMBS": "#86b6ef",
    "L2B_CORP": "#86b6ef",
    "L2B_EQUITY": "#86b6ef",
    "non_hqla": "#898781",   # muted gray = excluded from HQLA
}
TIER_LABEL = {
    "L1": "Level 1", "L2A": "Level 2A",
    "L2B_RMBS": "Level 2B", "L2B_CORP": "Level 2B", "L2B_EQUITY": "Level 2B",
    "non_hqla": "Non-HQLA",
}
STATUS_CRITICAL = "#d03b3b"

st.set_page_config(page_title="LCR Optimizer", layout="wide")


@st.cache_data
def get_market_yields():
    return fetch_treasury_yields()


st.title("LCR-Constrained Liquidity Optimizer")
st.caption(
    "Hybrid dataset: net cash outflows, HQLA tier totals, and haircuts are all real -- "
    "JPMorgan Chase's own publicly disclosed LCR figures for the quarter. The split of each "
    "tier into specific instruments (issuer, yield, min lot) is illustrative, since no bank "
    "publishes that level of detail -- see `data_jpm.py`'s `build_hybrid_asset_universe()` "
    "for exactly what's real vs. kept synthetic and why."
)

with st.sidebar:
    st.header("Data source")
    period_keys = list(JPM_PERIODS.keys())
    period_choice = st.radio(
        "Reporting quarter",
        options=period_keys,
        format_func=lambda k: JPM_PERIODS[k]["label"],
    )
    period = JPM_PERIODS[period_choice]

    st.header("Configuration")
    scenario_names = [s.name for s in ALL_JPM_SCENARIOS]
    scenario_choice = st.selectbox("Stress scenario", scenario_names, index=0)
    scenario = next(s for s in ALL_JPM_SCENARIOS if s.name == scenario_choice)
    st.caption(scenario.description)
    st.caption(
        "Stress here multiplies JPM's own real disclosed rate for each category (clamped to "
        "0-100%) rather than substituting an invented absolute number -- JPM's blended rates "
        "are realized outcomes, not BCBS minimums, so there's no independent 'more severe' "
        "figure to swap in. See `scenarios_jpm.py` for the exact multipliers."
    )
    benchmark_yield = st.slider("Benchmark yield (opportunity cost basis)", 0.03, 0.15, 0.05, 0.005, format="%.3f")
    cash_floor = st.number_input("Minimum cash floor ($mm)", min_value=0.0, value=0.0, step=50.0)
    use_concentration_cap = st.checkbox("Apply single-issuer concentration limit", value=False)
    concentration_cap = st.slider("Issuer concentration cap (% of total HQLA)", 5, 50, 40, 5) / 100.0 if use_concentration_cap else None
    st.caption(
        "Off by default: JPM's real HQLA is genuinely dominated by cash + Treasuries (correct "
        "for a money-center bank, not something to diversify away), so a tight cap here can "
        "make the problem infeasible -- try it to see the solver's infeasibility diagnostic."
    )

    st.divider()
    market_yields = get_market_yields()
    src = market_yields.get("_source", "unknown")
    st.caption(f"Treasury yield source (for Level 1 bond pricing): {src}")
    st.caption("Non-Treasury instrument yields are representative/synthetic -- no public source gives JPM's actual per-holding yields.")

assets = build_hybrid_asset_universe(period_choice, market_yields=market_yields)
contingent_liquidity_mm = period["other_liquidity_sources"]["total_mm"]

base_config = OptimizerConfig(
    net_cash_outflows=0.0,  # overwritten inside run_jpm_scenario per-scenario NCO
    cash_floor=cash_floor,
    issuer_concentration_cap=concentration_cap,
    benchmark_yield=benchmark_yield,
    contingent_liquidity_mm=contingent_liquidity_mm,
)
scenario_run = run_jpm_scenario(scenario, period_choice, assets, base_config)
result = scenario_run["result"]
nco = scenario_run["net_cash_outflows_mm"]
nco_detail = scenario_run["nco_detail"]

if result.status != "OPTIMAL":
    st.error(
        f"Solver status: {result.status} -- no feasible portfolio satisfies LCR >= 100% "
        f"under the '{scenario.name}' scenario for {JPM_PERIODS[period_choice]['label']}."
    )
    st.write(result.diagnostics.get("message", result.diagnostics))
    st.caption(
        "This can be a genuine result, not a bug: real available HQLA is capped at what JPM "
        "actually held that quarter (a hard real number), unlike the synthetic universe where "
        "supply bounds are a tunable design knob. A severe enough stress can legitimately "
        "exceed it -- try a milder scenario, or Baseline, to see a feasible allocation."
    )
    if "contingent_liquidity_sufficient" in result.diagnostics:
        sufficient = result.diagnostics["contingent_liquidity_sufficient"]
        st.info(
            ("✅ " if sufficient else "⚠️ ") + result.diagnostics["contingent_liquidity_message"]
        )
        st.caption(
            f"Contingent liquidity = JPM's disclosed ${contingent_liquidity_mm:,.0f}mm of "
            f"non-LCR-eligible fallback resources this quarter (excess unencumbered securities "
            f"+ FHLB/discount-window borrowing capacity). This never counts toward the LCR "
            f"number above -- it answers a different question (operational solvency), not "
            f"regulatory compliance. See `data_jpm.py`'s JPM_*_OTHER_LIQUIDITY_SOURCES."
        )
    st.stop()

# --- Headline metrics ---
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Net Cash Outflows", f"${nco:,.0f}mm")
col2.metric("Total HQLA (adjusted)", f"${result.total_hqla_adjusted:,.0f}mm")
lcr_delta = result.lcr_pct - 100.0
col3.metric("LCR", f"{result.lcr_pct:.1f}%", delta=f"{lcr_delta:+.1f} pts vs. 100% floor")
col4.metric("Annual Opportunity Cost", f"${result.total_annual_cost_mm:,.1f}mm/yr")

lp_result = solve_lp_relaxation(assets, OptimizerConfig(
    net_cash_outflows=nco, cash_floor=cash_floor,
    issuer_concentration_cap=concentration_cap, benchmark_yield=benchmark_yield,
))
marginal_cost = lp_result.lcr_marginal_cost_mm_per_mm if lp_result.status == "OPTIMAL" else None
col5.metric(
    "Marginal Cost of NCO",
    f"${marginal_cost:.4f}mm/yr per $mm" if marginal_cost is not None else "n/a",
    help=(
        "LP-relaxation shadow price on the LCR constraint: the extra annual opportunity cost "
        "of one more $mm of net cash outflows to cover, holding everything else fixed. From a "
        "continuous relaxation solved separately via OR-Tools GLOP (see model_lp.py) -- CP-SAT, "
        "the integer solver used for the actual allocation above, doesn't expose dual values."
    ),
)
if marginal_cost is not None and abs(marginal_cost - benchmark_yield) < 1e-9:
    st.caption(
        "Marginal cost here equals the benchmark yield exactly -- expected, not a bug: every "
        "hybrid-universe instrument has yield_pct=0.0 (JPM's disclosure reports fair values, "
        "not per-holding yields, so there's no real differentiation to optimize against). The "
        "number is mathematically correct but only as informative as the yield data feeding it."
    )

st.divider()

# --- Allocation chart ---
left, right = st.columns([3, 2])

with left:
    st.subheader("Optimal Allocation")
    alloc_df = pd.DataFrame([{
        "Asset": a.name, "Tier": TIER_LABEL[a.tier], "TierKey": a.tier,
        "Amount ($mm)": a.amount_mm, "Adjusted HQLA ($mm)": a.adjusted_mm,
        "Annual Cost ($mm)": a.annual_cost_mm,
    } for a in result.allocations]).sort_values("Amount ($mm)", ascending=True)

    fig = go.Figure()
    for tier_key in ["L1", "L2A", "L2B_RMBS", "L2B_CORP", "L2B_EQUITY"]:
        sub = alloc_df[alloc_df["TierKey"] == tier_key]
        if sub.empty:
            continue
        fig.add_trace(go.Bar(
            y=sub["Asset"], x=sub["Amount ($mm)"], orientation="h",
            name=TIER_LABEL[tier_key], marker_color=TIER_COLOR[tier_key],
            text=sub["Amount ($mm)"].map(lambda v: f"${v:,.0f}mm"), textposition="outside",
            legendgroup=TIER_LABEL[tier_key], showlegend=tier_key in ("L1", "L2A", "L2B_RMBS"),
        ))
    fig.update_layout(
        template="plotly_white",
        font=dict(color="black"),
        barmode="stack", height=max(320, 32 * len(alloc_df)),
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="Amount held ($mm)", legend_title="Tier",
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb",
    )
    st.plotly_chart(fig, width="stretch", theme=None)

with right:
    st.subheader("Tier Cap Utilization")
    l1 = result.diagnostics["l1_adjusted_mm"]
    l2a = result.diagnostics["l2a_adjusted_mm"]
    l2b = result.diagnostics["l2b_adjusted_mm"]
    total = l1 + l2a + l2b
    l2_pct = (l2a + l2b) / total * 100 if total else 0
    l2b_pct = l2b / total * 100 if total else 0

    cap_fig = go.Figure()
    cap_fig.add_trace(go.Bar(
        y=["Level 2 (2A+2B)<br>cap: 40%", "Level 2B<br>cap: 15%"],
        x=[l2_pct, l2b_pct], orientation="h",
        marker_color=[TIER_COLOR["L2A"], TIER_COLOR["L2B_RMBS"]],
        text=[f"{l2_pct:.1f}%", f"{l2b_pct:.1f}%"], textposition="outside", textfont=dict(color="black"),
    ))
    cap_fig.add_vline(x=40, line_dash="dash", line_color=STATUS_CRITICAL, annotation_text="40% cap")
    cap_fig.add_vline(x=15, line_dash="dash", line_color=STATUS_CRITICAL, annotation_text="15% cap")
    cap_fig.update_layout(
        height=220, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="% of total HQLA",
        xaxis_range=[0, 45], plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb", showlegend=False, font=dict(color="black"),
    )
    st.plotly_chart(cap_fig, width="stretch", theme=None)

    st.metric("Level 1", f"${l1:,.0f}mm ({100*l1/total:.0f}%)")
    st.metric("Level 2A", f"${l2a:,.0f}mm ({100*l2a/total:.0f}%)")
    st.metric("Level 2B", f"${l2b:,.0f}mm ({100*l2b/total:.0f}%)")

st.divider()

# --- Scenario comparison ---
st.subheader("Scenario Comparison")
st.caption(
    "All scenarios re-solved against the same quarter's real available HQLA. Wholesale Funding "
    "Freeze and Combined 2008-Style Shock are commonly INFEASIBLE here (shown as blank bars, red "
    "in the NCO chart) -- real available HQLA is capped at what JPM actually held that quarter, so "
    "a severe enough multiplier can legitimately exceed it. The table's rightmost column checks "
    "each infeasible scenario against JPM's own disclosed non-LCR-eligible fallback liquidity for "
    f"this quarter (${contingent_liquidity_mm:,.0f}mm of excess unencumbered securities + FHLB/"
    "discount-window capacity) -- 'True' means the bank would be operationally solvent even though "
    "not LCR-compliant; it never changes the Status/LCR columns themselves."
)

all_runs = [run_jpm_scenario(s, period_choice, assets, base_config) for s in ALL_JPM_SCENARIOS]
summary_df = summarize_jpm_scenarios(all_runs)
st.dataframe(summary_df, width="stretch", hide_index=True)

c1, c2 = st.columns(2)
with c1:
    feasible_df = summary_df.dropna(subset=["Annual Cost ($mm)"])
    fig_cost = go.Figure(go.Bar(
        x=feasible_df["Scenario"], y=feasible_df["Annual Cost ($mm)"],
        marker_color="#2a78d6", text=feasible_df["Annual Cost ($mm)"].map(lambda v: f"${v:,.0f}mm"),
        textposition="outside",
    ))
    fig_cost.update_layout(
        title="Annual Opportunity Cost by Scenario (feasible only)", height=320,
        margin=dict(l=10, r=10, t=40, b=10), yaxis_title="$mm / yr",
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb",
    )
    st.plotly_chart(fig_cost, width="stretch", theme=None)

with c2:
    fig_nco = go.Figure(go.Bar(
        x=summary_df["Scenario"], y=summary_df["Net Cash Outflows ($mm)"],
        marker_color=[STATUS_CRITICAL if s != "OPTIMAL" else "#2a78d6" for s in summary_df["Status"]],
        text=summary_df["Net Cash Outflows ($mm)"].map(lambda v: f"${v:,.0f}mm"),
        textposition="outside",
    ))
    fig_nco.update_layout(
        title="Net Cash Outflows by Scenario (red = infeasible)", height=320,
        margin=dict(l=10, r=10, t=40, b=10), yaxis_title="$mm",
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb",
    )
    st.plotly_chart(fig_nco, width="stretch", theme=None)

st.divider()

# --- Validation against JPM's own disclosed numbers ---
st.subheader("Validation vs. JPM's Own Disclosure")
st.caption(
    "This project recomputes NCO from JPM's own category-level disclosure lines, and builds "
    "the hybrid asset universe so each tier's total available capacity matches JPM's real "
    "disclosed number. The rows below check both against JPM's own reported totals for the "
    "same quarter (should match almost exactly). The optimizer's *chosen* allocation, shown "
    "in the metrics above, is deliberately smaller than the 'available' row: it minimizes "
    "cost down to the 100% LCR floor rather than replicating the surplus JPM actually holds."
)
disclosed = period["disclosed"]
total_available_hqla = sum(
    a.available * (1.0 - (a.haircut or 0.0)) for a in assets if a.is_hqla
)
compare_df = pd.DataFrame([
    {"Metric": "Total available HQLA ($mm, i.e. what JPM actually held)", "This project": round(total_available_hqla, 0), "JPM disclosed": disclosed["eligible_hqla_mm"]},
    {"Metric": "Net cash outflows, excl. maturity mismatch ($mm)", "This project": round(nco, 0), "JPM disclosed": disclosed["net_cash_outflow_excl_maturity_mismatch_mm"]},
])
st.dataframe(compare_df, width="stretch", hide_index=True)

with st.expander("Net cash outflow detail (this quarter)"):
    st.write(pd.DataFrame([nco_detail]).T.rename(columns={0: "$mm"}))
    st.caption("NCO = Total stressed outflows - min(Total stressed inflows, 75% x outflows). BCBS238 para 69.")

st.divider()

# --- Capital adequacy (Basel III Pillar 3) -- reporting only, not an optimizer input ---
st.subheader("Capital Adequacy (Basel III Pillar 3)")
if period_choice in JPM_CAPITAL_PERIODS:
    st.caption(
        "A different Basel III pillar from everything above: LCR is a liquidity requirement "
        "(HQLA vs. 30-day net cash outflows); this is a solvency requirement (capital vs. "
        "risk-weighted assets / leverage exposure). They don't share a denominator, and this "
        "project's HQLA portfolio is far too small relative to JPM's ~$2.0-2.1T total RWA to "
        "move these ratios -- so this section reports JPM's real disclosed capital position "
        "for the quarter, it isn't an optimizer constraint. See `capital.py` for why."
    )
    cap = compute_capital_adequacy(period_choice)
    cd1, cd2, cd3, cd4, cd5 = st.columns(5)
    cd1.metric("CET1 Ratio", f"{cap.cet1_ratio_pct:.1f}%", delta=f"{cap.cet1_surplus_pct:+.1f} pts vs. min")
    cd2.metric("Tier 1 Ratio", f"{cap.tier1_ratio_pct:.1f}%", delta=f"{cap.tier1_surplus_pct:+.1f} pts vs. min")
    cd3.metric("Total Capital Ratio", f"{cap.total_capital_ratio_pct:.1f}%", delta=f"{cap.total_capital_surplus_pct:+.1f} pts vs. min")
    cd4.metric("Tier 1 Leverage", f"{cap.tier1_leverage_ratio_pct:.1f}%", delta=f"{cap.tier1_leverage_surplus_pct:+.1f} pts vs. min")
    cd5.metric("SLR", f"{cap.slr_pct:.1f}%", delta=f"{cap.slr_surplus_pct:+.1f} pts vs. min")
    if cap.all_requirements_met:
        st.success(f"Well-capitalized as of {cap.as_of} -- every ratio above its regulatory minimum, matching JPM's own disclosure.")
    else:
        st.error(f"One or more capital ratios below their regulatory minimum as of {cap.as_of}.")
else:
    st.caption(
        f"No real capital data available for {JPM_PERIODS[period_choice]['label']} -- only 4Q25 "
        "and 1Q26 Pillar 3 Regulatory Capital Disclosures Reports were extracted (the source "
        "documents for 2Q25/3Q25's capital position were never provided, only their LCR "
        "Disclosure reports, which is why the rest of this dashboard covers all 4 quarters but "
        "this section doesn't). Not fabricated to fill the gap -- see `capital.py`."
    )
