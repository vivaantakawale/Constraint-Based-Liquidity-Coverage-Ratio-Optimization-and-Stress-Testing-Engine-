"""
Basel III LCR regulatory parameters 
(BCBS238, "Basel III: Liquidity Coverage Ratio and liquidity risk monitoring tools", Jan 2013)

Every rate below is cited to a paragraph in BCBS238. Where a jurisdiction
(US, EU) has since diverged from the Basel baseline in its own final rule,
that is noted -- we implement the BCBS baseline, not any single regulator's
transposition, since this is a teaching/portfolio project, not a compliance
system. This distinction is worth stating up front in an interview.

Two variants of retail run-off numbers exist in practice: BCBS238 sets
*minimum* 5% (stable) / 10% (less stable) internationally (para 48-50);
some regulators (e.g. US NPR) permit 3% for "fully insured, transactional"
deposits. We use BCBS238 minimums (5%/10%) as baseline.

No functions here -- constants only. Exports:
  HAIRCUTS: dict[str,float]  tier -> haircut.
  L2_CAP_OF_TOTAL_HQLA / L2B_CAP_OF_TOTAL_HQLA: float  0.40 / 0.15.
  OUTFLOW_RATES / INFLOW_RATES: dict[str,float]  category -> rate (BCBS238 + real JPM quarters merged).
  INFLOW_CAP_OF_OUTFLOWS: float  0.75.
  JPM_<Q><YY>_OUTFLOW_RATES / _INFLOW_RATES: dict[str,float]  per-quarter real rates, folded
    into OUTFLOW_RATES/INFLOW_RATES via .update().

data.py's OutflowLine.rate / InflowLine.rate read OUTFLOW_RATES/INFLOW_RATES
by category key -- every key used in data.py/data_jpm.py must exist here.
"""

# HQLA tiers: haircuts and composition caps (BCBS238 paras 24-33, 39-40)
# Haircuts applied to market value to get the HQLA-eligible ("adjusted") value
HAIRCUTS = {
    "L1": 0.00,        # Level 1: cash, CB reserves, 0%-risk-weighted sovereign debt (para 24-25)
    "L2A": 0.15,       # Level 2A: 20%-risk-weighted sovereign/PSE debt, AA-/better corporate debt (para 31)
    "L2B_RMBS": 0.25,  # Level 2B: RMBS rated AA or better (para 33(a))
    "L2B_CORP": 0.50,  # Level 2B: corporate debt rated A+ to BBB- (para 33(b))
    "L2B_EQUITY": 0.50,  # Level 2B: qualifying common equity shares (para 33(c))
}

# Composition caps, applied to *total HQLA after haircuts* (para 39-40)
# NOTE (simplifying assumption): Basel text defines these via
# sequential "Adjusted Amount" algorithm in Annex 1 because the regulatory
# reporting formula has to resolve a circular definition (the cap is a % of
# a total that includes the capped items) without an optimizer. Here we
# instead impose the equivalent condition directly as a linear constraint
# on the decision variables -- the solver resolves the circularity for us.
# The two formulations are equivalent at the constraint boundary; this is a
# clean thing to point out if asked why the code has no "adjusted amount"
# step-function.
L2_CAP_OF_TOTAL_HQLA = 0.40   # Level 2 (2A + 2B combined) <= 40% of total HQLA
L2B_CAP_OF_TOTAL_HQLA = 0.15  # Level 2B alone <= 15% of total HQLA

# Cash outflow run-off rates (BCBS238 paras 47-116)
# Keys here are the liability/exposure "category" a synthetic balance-sheet
# line item is tagged with in data.py

OUTFLOW_RATES = {
    # Retail deposits (para 48-50)
    "retail_stable": 0.05,          # insured, transactional -- BCBS min (para 49)
    "retail_less_stable": 0.10,     # para 50
    # Small business treated as retail (para 53)
    "sme_stable": 0.05,
    "sme_less_stable": 0.10,
    # Operational deposits from clearing/custody/cash-mgmt relationships (para 55-57)
    "operational_deposits": 0.25,
    # Unsecured wholesale funding (para 58-59)
    "wholesale_nonfinancial": 0.40,   # non-financial corporates, sovereigns, PSEs, MDBs
    "wholesale_financial": 1.00,      # banks, other financial institutions, other legal entities
    # Secured funding / repo, run-off depends on collateral pledged (para 62-64)
    "secured_L1_collateral": 0.00,
    "secured_L2A_collateral": 0.15,
    "secured_other_collateral": 1.00,
    # Committed credit & liquidity facilities drawn down in stress (para 116-131, simplified)
    "facility_retail_sme": 0.05,
    "facility_corporate_credit": 0.10,
    "facility_corporate_liquidity": 0.30,
    "facility_financial": 0.40,
}

# Cash inflow rates (BCBS238 paras 143-160) and the inflow cap (para 143)

INFLOW_RATES = {
    "retail_sme_loans": 0.50,          # para 156
    "wholesale_nonfinancial_loans": 0.50,  # para 157
    "wholesale_financial_receivables": 1.00,  # para 158
    "reverse_repo_L1_collateral": 0.00,   # para 146
    "reverse_repo_L2A_collateral": 0.15,  # para 146
    "committed_facilities_to_bank": 0.00,  # cannot count facilities extended TO you (para 155)
}

# Total inflows are capped at 75% of total (stressed) outflows (para 143)
INFLOW_CAP_OF_OUTFLOWS = 0.75

# JPMorgan Chase & Co. -- REAL, publicly disclosed 4Q25 LCR rates
# Unlike OUTFLOW_RATES/INFLOW_RATES above (which are cited to BCBS238
# paragraphs), every rate here is *implied*, computed as
# (average weighted amount / average unweighted amount) from JPMorganChase's
# own "Liquidity Coverage Ratio Disclosure, quarterly period ended
# December 31, 2025" (page 2 table), a document required and filed under the
# US LCR public disclosure rule (12 CFR 249 Subpart R). These are the actual
# blended run-off/draw rates JPM realized under its own interpretation of
# the US LCR rule (which itself is stricter than the BCBS238 baseline in
# several places, e.g. wholesale non-operational funding), not a BCBS
# citation -- do not mix these keys with OUTFLOW_RATES/INFLOW_RATES above.
#
# Sanity check: the implied L2A haircut recovered from JPM's own HQLA table
# (1 - 2,867/3,373 = 14.99%) matches BCBS238's 15% almost exactly, which is
# a useful cross-check that the disclosure's "weighted / unweighted" ratio
# is indeed a haircut/run-off-rate in the same sense used elsewhere here.
JPM_4Q25_OUTFLOW_RATES = {
    "jpm_4q25_stable_retail_deposits": 0.0300,          # 20,109 / 670,316
    "jpm_4q25_other_retail_funding": 0.1138,            # 46,144 / 405,325
    "jpm_4q25_brokered_deposits": 0.2642,               # 21,936 / 83,024
    "jpm_4q25_operational_deposits": 0.2492,            # 206,260 / 827,655
    "jpm_4q25_nonoperational_wholesale_funding": 0.6030,  # 324,217 / 537,727
    "jpm_4q25_unsecured_debt": 1.0000,                  # 8,538 / 8,538
    "jpm_4q25_secured_wholesale_funding": 0.3514,       # 402,105 / 1,144,167
    "jpm_4q25_derivative_outflow": 0.7350,              # 77,650 / 105,655
    "jpm_4q25_credit_liquidity_facilities": 0.2497,     # 154,596 / 619,294
    "jpm_4q25_other_contractual_funding": 1.0000,       # 5,582 / 5,582
    "jpm_4q25_other_contingent_funding": 0.0366,        # 16,127 / 441,055
}

JPM_4Q25_INFLOW_RATES = {
    "jpm_4q25_secured_lending_inflow": 0.3358,     # 351,464 / 1,046,727
    "jpm_4q25_retail_inflow": 0.5000,              # 17,322 / 34,645
    "jpm_4q25_unsecured_wholesale_inflow": 0.8866,  # 33,142 / 37,381
    "jpm_4q25_derivative_inflow": 1.0000,          # 13,727 / 13,727
    "jpm_4q25_securities_inflow": 1.0000,          # 5,178 / 5,178
    "jpm_4q25_broker_dealer_segregated_inflow": 1.0000,  # 32,860 / 32,860
    "jpm_4q25_other_inflow": 0.0000,               # 0 / 473
}

# Add the JPM keys into the same lookup dicts OutflowLine/InflowLine read
# from (data.py's .rate property does `rates.OUTFLOW_RATES[self.category]`),
# so real JPM lines can be built with the existing dataclasses unmodified.
OUTFLOW_RATES.update(JPM_4Q25_OUTFLOW_RATES)
INFLOW_RATES.update(JPM_4Q25_INFLOW_RATES)

# JPMorgan Chase & Co. -- REAL, publicly disclosed 1Q26 LCR rates

# Same derivation and same caveats as the 4Q25 block above, source is
# JPMorganChase, "Liquidity Coverage Ratio Disclosure," quarterly period
# ended March 31, 2026 ("1Q26 LCR Report"), page 2 table. Kept as a
# separate quarter (distinct key prefix) rather than overwriting 4Q25, so
# both quarters remain independently usable/comparable
JPM_1Q26_OUTFLOW_RATES = {
    "jpm_1q26_stable_retail_deposits": 0.0300,          # 20,459 / 681,976
    "jpm_1q26_other_retail_funding": 0.1132,            # 48,020 / 424,121
    "jpm_1q26_brokered_deposits": 0.2419,               # 18,354 / 75,870
    "jpm_1q26_operational_deposits": 0.2491,            # 210,508 / 845,042
    "jpm_1q26_nonoperational_wholesale_funding": 0.5891,  # 313,914 / 532,841
    "jpm_1q26_unsecured_debt": 1.0000,                  # 9,977 / 9,977
    "jpm_1q26_secured_wholesale_funding": 0.3333,       # 422,434 / 1,267,571
    "jpm_1q26_derivative_outflow": 0.7405,              # 83,947 / 113,359
    "jpm_1q26_credit_liquidity_facilities": 0.2424,     # 148,323 / 611,832
    "jpm_1q26_other_contractual_funding": 1.0000,       # 3,747 / 3,747
    "jpm_1q26_other_contingent_funding": 0.0371,        # 16,237 / 437,601
}

JPM_1Q26_INFLOW_RATES = {
    "jpm_1q26_secured_lending_inflow": 0.3286,     # 382,628 / 1,164,549
    "jpm_1q26_retail_inflow": 0.5000,              # 18,298 / 36,595
    "jpm_1q26_unsecured_wholesale_inflow": 0.8840,  # 35,830 / 40,532
    "jpm_1q26_derivative_inflow": 1.0000,          # 15,178 / 15,178
    "jpm_1q26_securities_inflow": 1.0000,          # 5,965 / 5,965
    "jpm_1q26_broker_dealer_segregated_inflow": 1.0000,  # 36,956 / 36,956
    "jpm_1q26_other_inflow": 0.0000,               # 0 / 950
}

OUTFLOW_RATES.update(JPM_1Q26_OUTFLOW_RATES)
INFLOW_RATES.update(JPM_1Q26_INFLOW_RATES)

# JPMorgan Chase & Co. -- REAL, publicly disclosed 2Q25 LCR rates

# Source: "Liquidity Coverage Ratio Disclosure," quarterly period ended
# June 30, 2025 ("2Q25 LCR Report"), page 2 table. Same derivation/caveats
# as the 4Q25 block above.
JPM_2Q25_OUTFLOW_RATES = {
    "jpm_2q25_stable_retail_deposits": 0.0300,          # 20,219 / 673,954
    "jpm_2q25_other_retail_funding": 0.1129,            # 45,279 / 401,086
    "jpm_2q25_brokered_deposits": 0.2568,               # 24,950 / 97,170
    "jpm_2q25_operational_deposits": 0.2492,            # 193,737 / 777,558
    "jpm_2q25_nonoperational_wholesale_funding": 0.6037,  # 313,510 / 519,279
    "jpm_2q25_unsecured_debt": 1.0000,                  # 8,464 / 8,464
    "jpm_2q25_secured_wholesale_funding": 0.2997,       # 328,017 / 1,094,390
    "jpm_2q25_derivative_outflow": 0.7659,              # 81,946 / 106,996
    "jpm_2q25_credit_liquidity_facilities": 0.2516,     # 142,709 / 567,135
    "jpm_2q25_other_contractual_funding": 1.0000,       # 4,294 / 4,294
    "jpm_2q25_other_contingent_funding": 0.0360,        # 15,107 / 419,137
}

JPM_2Q25_INFLOW_RATES = {
    "jpm_2q25_secured_lending_inflow": 0.2909,     # 304,604 / 1,047,050
    "jpm_2q25_retail_inflow": 0.5000,              # 15,766 / 31,532
    "jpm_2q25_unsecured_wholesale_inflow": 0.8836,  # 31,223 / 35,337
    "jpm_2q25_derivative_inflow": 1.0000,          # 18,608 / 18,608
    "jpm_2q25_securities_inflow": 1.0000,          # 6,944 / 6,944
    "jpm_2q25_broker_dealer_segregated_inflow": 1.0000,  # 31,562 / 31,562
    "jpm_2q25_other_inflow": 0.0000,               # 0 / 0 (both zero this quarter)
}

OUTFLOW_RATES.update(JPM_2Q25_OUTFLOW_RATES)
INFLOW_RATES.update(JPM_2Q25_INFLOW_RATES)

# JPMorgan Chase & Co. -- REAL, publicly disclosed 3Q25 LCR rates

# Source: "Liquidity Coverage Ratio Disclosure," quarterly period ended
# September 30, 2025 ("3Q25 LCR Report"), page 2 table
# SamE derivation/caveats as 4Q25 block above
JPM_3Q25_OUTFLOW_RATES = {
    "jpm_3q25_stable_retail_deposits": 0.0300,          # 20,108 / 670,253
    "jpm_3q25_other_retail_funding": 0.1136,            # 45,503 / 400,388
    "jpm_3q25_brokered_deposits": 0.2766,               # 26,478 / 95,724
    "jpm_3q25_operational_deposits": 0.2492,            # 203,127 / 815,134
    "jpm_3q25_nonoperational_wholesale_funding": 0.6097,  # 307,178 / 503,800
    "jpm_3q25_unsecured_debt": 1.0000,                  # 7,713 / 7,713
    "jpm_3q25_secured_wholesale_funding": 0.3276,       # 372,686 / 1,137,507
    "jpm_3q25_derivative_outflow": 0.7363,              # 75,135 / 102,039
    "jpm_3q25_credit_liquidity_facilities": 0.2533,     # 148,968 / 588,185
    "jpm_3q25_other_contractual_funding": 1.0000,       # 3,283 / 3,283
    "jpm_3q25_other_contingent_funding": 0.0363,        # 15,680 / 431,563
}

JPM_3Q25_INFLOW_RATES = {
    "jpm_3q25_secured_lending_inflow": 0.2983,     # 312,029 / 1,045,933
    "jpm_3q25_retail_inflow": 0.5000,              # 16,443 / 32,886
    "jpm_3q25_unsecured_wholesale_inflow": 0.8889,  # 30,619 / 34,447
    "jpm_3q25_derivative_inflow": 1.0000,          # 12,927 / 12,927
    "jpm_3q25_securities_inflow": 1.0000,          # 6,275 / 6,275
    "jpm_3q25_broker_dealer_segregated_inflow": 1.0000,  # 28,327 / 28,327
    "jpm_3q25_other_inflow": 0.0000,               # 0 / 288
}

OUTFLOW_RATES.update(JPM_3Q25_OUTFLOW_RATES)
INFLOW_RATES.update(JPM_3Q25_INFLOW_RATES)
