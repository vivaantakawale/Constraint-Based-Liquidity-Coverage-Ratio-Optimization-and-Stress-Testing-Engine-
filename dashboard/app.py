"""
LCR Optimizer Streamlit Dashboard

Run with:  streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lcr_optimizer.capital import JPM_CAPITAL_PERIODS, compute_capital_adequacy
from lcr_optimizer.data_jpm import JPM_PERIODS, build_hybrid_asset_universe
from lcr_optimizer.market_data import fetch_treasury_yields
from lcr_optimizer.model import OptimizerConfig
from lcr_optimizer.model_lp import solve_lp_relaxation
from lcr_optimizer.scenarios_jpm import ALL_JPM_SCENARIOS, run_jpm_scenario, summarize_jpm_scenarios

pio.templates.default = "plotly_white"


INK = "#262422"          
ACCENT = "#F0876E"        
SURFACE = "#F4F1EC"      
MUTED = "#C7C2B9"         
FAINT = "#E4E0D9"         

TIER_COLOR = {
    "L1": ACCENT,
    "L2A": ACCENT,
    "L2B_RMBS": ACCENT, "L2B_CORP": ACCENT, "L2B_EQUITY": ACCENT,
    "non_hqla": FAINT,
}
TIER_LABEL = {
    "L1": "Level 1", "L2A": "Level 2A",
    "L2B_RMBS": "Level 2B", "L2B_CORP": "Level 2B", "L2B_EQUITY": "Level 2B",
    "non_hqla": "Non-HQLA",
}

def escape_dollars(text: str) -> str:
    """Streamlit renders st.caption/error/info/markdown as markdown, which
    treats a $-paired string as LaTeX math -- any dynamic text with two or
    more literal '$' amounts needs escaping or it silently renders as a
    broken math block instead of the text."""
    return text.replace("$", r"\$")


CHART_LAYOUT = dict(
    template="plotly_white",
    font=dict(color=INK),
    plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
    margin=dict(l=10, r=10, t=30, b=10),
)

st.set_page_config(page_title="LCR Optimizer", layout="wide")


@st.cache_data(ttl=3600)
def get_market_yields():
    return fetch_treasury_yields()


st.title("LCR-Constrained Liquidity Optimizer")
st.caption("Minimum-cost HQLA portfolio that clears Basel III LCR >= 100%, solved against JPMorgan Chase's real quarterly disclosures.")

with st.expander("What's real here, and what's illustrative?"):
    st.markdown(
        "**Real:** net cash outflows, HQLA tier totals, and haircuts are JPMorgan Chase's own "
        "publicly disclosed LCR figures for the selected quarter.\n\n"
        "**Illustrative:** the split of each tier into specific instruments (issuer, yield, min "
        "lot) -- no bank publishes that level of detail, so this project keeps a synthetic "
        "instrument structure rescaled to match the real tier totals. "
        "See `data_jpm.py`'s `build_hybrid_asset_universe()` for the exact method."
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

    st.header("Scenario")
    scenario_names = [s.name for s in ALL_JPM_SCENARIOS]
    scenario_choice = st.selectbox("Stress scenario", scenario_names, index=0)
    scenario = next(s for s in ALL_JPM_SCENARIOS if s.name == scenario_choice)
    st.caption(scenario.description)

    with st.expander("Advanced settings"):
        benchmark_yield = st.slider("Benchmark yield (opportunity cost basis)", 0.03, 0.15, 0.05, 0.005, format="%.3f")
        cash_floor = st.number_input("Minimum cash floor ($mm)", min_value=0.0, value=0.0, step=50.0)
        use_concentration_cap = st.checkbox("Apply single-issuer concentration limit", value=False)
        concentration_cap = st.slider("Issuer concentration cap (% of total HQLA)", 5, 50, 40, 5) / 100.0 if use_concentration_cap else None
        if use_concentration_cap:
            st.caption("Real HQLA is dominated by cash + Treasuries, so a tight cap can make the problem infeasible -- that's expected, not a bug.")

    market_yields = get_market_yields()
    st.caption(f"Treasury yields: {market_yields.get('_source', 'unknown')}")

assets = build_hybrid_asset_universe(period_choice, market_yields=market_yields)
contingent_liquidity_mm = period["other_liquidity_sources"]["total_mm"]

base_config = OptimizerConfig(
    net_cash_outflows=0.0,  # overwritten inside run_jpm_scenario per scenario NCO
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
    shortfall = result.diagnostics.get("shortfall_mm")
    max_hqla = result.diagnostics.get("max_achievable_hqla_mm")
    st.warning(
        escape_dollars(
            f"**This scenario fails the stress test -- that's a real finding, not an "
            f"application error.** Under '{scenario.name}' for {JPM_PERIODS[period_choice]['label']}, "
            f"JPM would need ${nco / 1000:,.1f}bn of safe assets to cover this scenario's "
            f"outflows, but its real, disclosed stock of eligible assets tops out at "
            f"${max_hqla / 1000:,.1f}bn -- a ${shortfall / 1000:,.1f}bn gap. This project only "
            f"uses assets JPM actually reported holding, so it can't manufacture a passing "
            f"portfolio out of assets that don't exist. Try a milder scenario to see a "
            f"feasible allocation."
        )
    )
    if "contingent_liquidity_sufficient" in result.diagnostics:
        sufficient = result.diagnostics["contingent_liquidity_sufficient"]
        st.info(
            ("✅ " if sufficient else "⚠️ ") + escape_dollars(result.diagnostics["contingent_liquidity_message"])
        )
    st.stop()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Net Cash Outflows", f"${nco / 1000:,.1f}bn")
col2.metric("Total HQLA (adjusted)", f"${result.total_hqla_adjusted / 1000:,.1f}bn")
lcr_delta = result.lcr_pct - 100.0
col3.metric("LCR", f"{result.lcr_pct:.1f}%", delta=f"{lcr_delta:+.1f} pts vs. 100%")
col4.metric("Annual Opportunity Cost", f"${result.total_annual_cost_mm / 1000:,.2f}bn/yr")

lp_result = solve_lp_relaxation(assets, OptimizerConfig(
    net_cash_outflows=nco, cash_floor=cash_floor,
    issuer_concentration_cap=concentration_cap, benchmark_yield=benchmark_yield,
))
marginal_cost = lp_result.lcr_marginal_cost_mm_per_mm if lp_result.status == "OPTIMAL" else None
col5.metric(
    "Marginal Cost of NCO",
    f"{marginal_cost * 100:.2f}%/yr" if marginal_cost is not None else "n/a",
    help=(
        "LP-relaxation shadow price on the LCR constraint: the extra annual cost of one more "
        "$mm of net cash outflows to cover, expressed as a rate. CP-SAT (used for the allocation "
        "above) is a pure integer solver and doesn't expose dual values, so this comes from a "
        "separate GLOP solve."
    ),
)

st.divider()

overview_tab, scenarios_tab, data_tab = st.tabs(["Overview", "Stress Scenarios", "Validation & Capital"])

with overview_tab:
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
                cliponaxis=False,
                legendgroup=TIER_LABEL[tier_key], showlegend=tier_key in ("L1", "L2A", "L2B_RMBS"),
            ))
        fig.update_layout(
            **{**CHART_LAYOUT, "margin": dict(l=10, r=70, t=40, b=10)},
            barmode="stack", height=max(320, 32 * len(alloc_df)),
            xaxis_title="Amount held ($mm)",
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title=None),
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
            marker_color=[MUTED, ACCENT],
            text=[f"{l2_pct:.1f}%", f"{l2b_pct:.1f}%"], textposition="outside", textfont=dict(color=INK),
            cliponaxis=False,
        ))
        cap_fig.add_vline(x=40, line_dash="dash", line_color=ACCENT, annotation_text="40% cap")
        cap_fig.add_vline(x=15, line_dash="dash", line_color=ACCENT, annotation_text="15% cap")
        cap_fig.update_layout(
            **CHART_LAYOUT,
            height=220, xaxis_title="% of total HQLA", xaxis_range=[0, 45], showlegend=False,
        )
        st.plotly_chart(cap_fig, width="stretch", theme=None)

        st.caption(escape_dollars(f"Level 1: ${l1:,.0f}mm · Level 2A: ${l2a:,.0f}mm · Level 2B: ${l2b:,.0f}mm"))

with scenarios_tab:
    st.caption(
        "All scenarios re-solved against this quarter's real available HQLA. Severe scenarios "
        "commonly fail here -- that's a real stress-test result (JPM's actual assets can't "
        "cover it), not an application error, since real supply is a hard number, not a "
        "tunable knob."
    )
    all_runs = [run_jpm_scenario(s, period_choice, assets, base_config) for s in ALL_JPM_SCENARIOS]
    summary_df = summarize_jpm_scenarios(all_runs)

    display_df = summary_df.copy()
    display_df["Status"] = display_df["Status"].replace({"OPTIMAL": "Passes", "FEASIBLE": "Passes", "INFEASIBLE": "Fails stress test"})
    display_df = display_df.fillna("—")
    st.dataframe(display_df, width="stretch", hide_index=True)

    fig_cost = go.Figure(go.Bar(
        x=summary_df["Scenario"], y=summary_df["Net Cash Outflows ($mm)"],
        marker_color=[ACCENT if s != "OPTIMAL" else INK for s in summary_df["Status"]],
        text=summary_df["Net Cash Outflows ($mm)"].map(lambda v: f"${v:,.0f}mm"),
        textposition="outside", cliponaxis=False,
    ))
    fig_cost.update_layout(
        **{**CHART_LAYOUT, "margin": dict(l=10, r=10, t=50, b=10)},
        title="Net Cash Outflows by Scenario (accent = fails stress test)", height=340,
        yaxis_title="$mm",
    )
    st.plotly_chart(fig_cost, width="stretch", theme=None)

    with st.expander("Contingent liquidity vs. infeasible scenarios"):
        st.caption(
            f"The rightmost table column checks each infeasible scenario against JPM's disclosed "
            f"${contingent_liquidity_mm:,.0f}mm of non-LCR-eligible fallback liquidity (excess "
            f"unencumbered securities + FHLB/discount-window capacity). 'True' means the bank "
            f"would be operationally solvent even though not LCR-compliant -- it never changes "
            f"the Status/LCR columns above."
        )

with data_tab:
    st.subheader("Validation vs. JPM's Own Disclosure")
    st.caption("Recomputed from JPM's category-level disclosure lines; should match almost exactly.")
    disclosed = period["disclosed"]
    total_available_hqla = sum(
        a.available * (1.0 - (a.haircut or 0.0)) for a in assets if a.is_hqla
    )
    compare_df = pd.DataFrame([
        {"Metric": "Total available HQLA ($mm)", "This project": round(total_available_hqla, 0), "JPM disclosed": disclosed["eligible_hqla_mm"]},
        {"Metric": "Net cash outflows, excl. maturity mismatch ($mm)", "This project": round(nco, 0), "JPM disclosed": disclosed["net_cash_outflow_excl_maturity_mismatch_mm"]},
    ])
    st.dataframe(compare_df, width="stretch", hide_index=True)

    with st.expander("Net cash outflow detail"):
        st.write(pd.DataFrame([nco_detail]).T.rename(columns={0: "$mm"}))
        st.caption("NCO = Total stressed outflows - min(Total stressed inflows, 75% x outflows). BCBS238 para 69.")

    st.divider()

    st.subheader("Capital Adequacy (Basel III Pillar 3)")
    if period_choice in JPM_CAPITAL_PERIODS:
        st.caption("A separate Basel III pillar (solvency, not liquidity) -- reported here for context, not an optimizer constraint.")
        cap = compute_capital_adequacy(period_choice)
        cd1, cd2, cd3, cd4, cd5 = st.columns(5)
        cd1.metric("CET1 Ratio", f"{cap.cet1_ratio_pct:.1f}%", delta=f"{cap.cet1_surplus_pct:+.1f} pts vs. min")
        cd2.metric("Tier 1 Ratio", f"{cap.tier1_ratio_pct:.1f}%", delta=f"{cap.tier1_surplus_pct:+.1f} pts vs. min")
        cd3.metric("Total Capital Ratio", f"{cap.total_capital_ratio_pct:.1f}%", delta=f"{cap.total_capital_surplus_pct:+.1f} pts vs. min")
        cd4.metric("Tier 1 Leverage", f"{cap.tier1_leverage_ratio_pct:.1f}%", delta=f"{cap.tier1_leverage_surplus_pct:+.1f} pts vs. min")
        cd5.metric("SLR", f"{cap.slr_pct:.1f}%", delta=f"{cap.slr_surplus_pct:+.1f} pts vs. min")
        if cap.all_requirements_met:
            st.success(f"Well-capitalized as of {cap.as_of} -- every ratio above its regulatory minimum.")
        else:
            st.error(f"One or more capital ratios below their regulatory minimum as of {cap.as_of}.")
    else:
        st.caption(f"No real capital data available for {JPM_PERIODS[period_choice]['label']} -- only 4Q25 and 1Q26 Pillar 3 reports were extracted.")
