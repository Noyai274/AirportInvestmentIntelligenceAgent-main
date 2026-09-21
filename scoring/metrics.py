"""
Owner: Amit. Plan §4.1 — THE CONTRACT between the data layer and the tools.

build(monthly: DataFrame, anchor=config.TTM_ANCHOR) -> DataFrame indexed by airport code
  One row per airport with exactly the columns in plan §4.1:
    pax_ttm, hub_tier, pax_growth_yoy, pax_vs_2019, recovered_2019,
    load_factor_ttm, lf_trend, seat_growth_yoy,
    asm_ttm, rpm_ttm,
    intl_dep_share, intl_asm_share, avg_stage_sm, dom_avg_stage_sm, intl_avg_stage_sm,
    seats_per_intl_dep, pax_per_departure, dep_growth_yoy
  plus ttm_end (the anchor month, so every answer can say "TTM ending Apr 2026").

Rules:
  - TTM = the 12 months ending at the anchor; prior TTM = the 12 before that; 2019 = Jan–Dec 2019.
  - load_factor_ttm = sum(passengers) / sum(seats) over the window — NOT the mean of monthly LFs.
  - hub_tier from each airport's share of total US pax_ttm (config.HUB_TIERS).
  - recovered_2019 = pax_vs_2019 >= config.RECOVERY_THRESHOLD.
  - Once these column names are fixed, do not rename them: agent/tools.py builds against them.

Also here (small helpers, used once for DESIGN.md):
  floor_check(monthly) — std of pax_growth_yoy by pax_ttm bucket; picks RANKING_FLOOR (plan §4.3 item 1)
  noise_check(monthly) — median |YoY change| across large hubs; validates the 2% threshold (item 3)
"""
