"""
Owner: Amit. Plan §4.2. The Capacity Pressure Index.

CPI measures how hard current demand presses on an airport's existing capacity, and whether that
pressure is rising. Formula and one-sentence rationale per component: plan §4.2.

score(metrics: DataFrame, weights=config.WEIGHTS, baseline="yoy") -> DataFrame
  - drops airports below config.RANKING_FLOOR_PAX_TTM
  - growth term = pax_growth_yoy (baseline="yoy") or pax_vs_2019 (baseline="2019"); nothing else changes
  - z-scores each component WITHIN hub_tier (plan §4.0 rule 2)
  - CPI = weighted sum; adds columns cpi, rank_in_tier, and the five z-components (for decomposition)

decompose(code, ...) -> dict          — per-component contribution; backs unmet_demand and "why" answers
sensitivity(codes, weight_sets=config.SENSITIVITY_SETS) -> DataFrame
                                      — rank of each code under each weight set; the DESIGN.md table
congestion(code) -> dict              — the utilisation components only (LF, pax−seat growth, dep growth)

No I/O, no LLM, no network. Takes the metrics frame, returns frames/dicts.
"""
