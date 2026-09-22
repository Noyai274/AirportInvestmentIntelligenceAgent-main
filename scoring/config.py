"""
Owner: Amit (drafted by Claude). Plan §4.2, §4.3. Every tunable in ONE place; nothing else hard-codes a number.

Read this file as the definition of the KPI. DESIGN.md §4 explains each constant in one sentence;
the agent's explain_scoring tool returns them verbatim.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Data window
# ──────────────────────────────────────────────────────────────────────────────
SINCE = "2019-01-01"          # first month pulled from BTS — gives the pre-COVID baseline year
BASELINE_YEAR = 2019          # the "what the infrastructure was built for" reference (plan §4.3 item 3)
TTM_ANCHOR = "latest"         # TTM ends at the latest month in the data; or "YYYY-MM-DD" to pin it


# ──────────────────────────────────────────────────────────────────────────────
# The KPI: Capacity Pressure Index (plan §4.2)
#
# CPI = Σ weight_k · z_k, where each z is a within-hub-tier z-score of one component.
# Equal weights ARE the definition: four ratios that each evidence the same thesis, and no ground
# truth to prefer one. Change only for a reason you can name, and say it in DESIGN.md.
#
# Every component is a RATIO (plan §4.0 rule 1). A fifth component, size = log(pax_ttm), was removed
# on 2026-09-22: a within-tier z-score of size is a monotone "bigger ranks higher" term, not the
# stabiliser it was documented as (LAX, shrinking, drew its whole positive CPI from it). Size now
# enters only as context — hub tiers, the ranking floor, pax_ttm in every answer. See DESIGN.md.
# ──────────────────────────────────────────────────────────────────────────────
WEIGHTS = {
    "growth":              0.25,   # pax_growth_yoy (or pax_vs_2019 under baseline="2019")
    "load_factor":         0.25,   # load_factor_ttm — the planes are full
    "lf_trend":            0.25,   # load_factor_ttm − prior-year — and getting fuller
    "demand_minus_supply": 0.25,   # growth − seat_growth_yoy — airlines can't add seats fast enough
}

# Alternative definitions for the sensitivity table (plan §4.2). They do not justify WEIGHTS;
# they show how much the ranking depends on them.
SENSITIVITY_SETS = {
    "equal":             WEIGHTS,
    "growth-heavy":      {"growth": .40, "load_factor": .15, "lf_trend": .15, "demand_minus_supply": .30},
    "utilisation-heavy": {"growth": .10, "load_factor": .35, "lf_trend": .20, "demand_minus_supply": .35},
}

# z-scores are clipped to ±Z_CLIP before weighting so one airport with +40% growth off a tiny base
# cannot dominate its tier. Set to None to disable. Stated in DESIGN.md as a robustness choice.
Z_CLIP = 3.0

# Airports below this many passengers in the trailing 12 months are not scored at all — their
# growth numbers are noise (one carrier opening one route). SET EMPIRICALLY (plan §4.3 item 1) with
# metrics.floor_check() on the 2026-09-21 snapshot: std of pax_growth_yoy by size bucket was
#   100–300k: 0.21   300k–1M: 0.08   1–3M: 0.04   >10M: 0.04
# — a 3× drop at 300k, flat above 1M. The floor sits where the variance stabilises. 178 airports qualify.
RANKING_FLOOR_PAX_TTM = 300_000

# pax_vs_2019 is only meaningful when there WAS traffic in 2019. Lakeland (LAL) had near-zero 2019
# passengers and shows +59,000% "vs 2019" — a division by almost nothing, not recovery. Below this
# 2019 base the metric is NaN and the recovery flag is True (no baseline ⇒ no disclaimer).
MIN_BASELINE_PAX = RANKING_FLOOR_PAX_TTM

# An airport is scored only if both its TTM and prior-TTM windows are complete (12 months each).
# Otherwise year-over-year growth compares unequal windows.
REQUIRE_COMPLETE_WINDOWS = True


# ──────────────────────────────────────────────────────────────────────────────
# Segmentation (plan §4.0 rule 2, §3.3): FAA hub classes by share of total US TTM passengers.
# z-scores are computed within these groups. Order matters: first match wins.
# ──────────────────────────────────────────────────────────────────────────────
HUB_TIERS = [
    ("Large",  0.0100),   # ≥ 1%
    ("Medium", 0.0025),   # 0.25% – 1%
    ("Small",  0.0005),   # 0.05% – 0.25%
]
NON_HUB = "Non-hub"       # below 0.05%


# ──────────────────────────────────────────────────────────────────────────────
# 2019 recovery flag (plan §4.3 item 3). Absolute, not σ-based, on purpose: "recovered" is the
# airport versus its own design-era traffic, not versus its peers. −2% = within ordinary
# year-to-year noise, so no disclaimer there. CHECKED with metrics.noise_check() on 2026-09-21:
# median |YoY change| across the 30 large hubs = 2.6%, same order as the threshold. Kept.
# ──────────────────────────────────────────────────────────────────────────────
RECOVERY_THRESHOLD = -0.02


# ──────────────────────────────────────────────────────────────────────────────
# Regions, as US Census Bureau divisions, plus the four Census regions as aliases. State codes
# come from data/airport_states.csv (OurAirports). The agent's resolve step maps "New England",
# "the Northeast", "Sun Belt" (not defined — it should say so) onto these keys.
# ──────────────────────────────────────────────────────────────────────────────
REGIONS = {
    "New England":        ["CT", "MA", "ME", "NH", "RI", "VT"],
    "Mid-Atlantic":       ["NJ", "NY", "PA"],
    "East North Central": ["IL", "IN", "MI", "OH", "WI"],
    "West North Central": ["IA", "KS", "MN", "MO", "ND", "NE", "SD"],
    "South Atlantic":     ["DC", "DE", "FL", "GA", "MD", "NC", "SC", "VA", "WV"],
    "East South Central": ["AL", "KY", "MS", "TN"],
    "West South Central": ["AR", "LA", "OK", "TX"],
    "Mountain":           ["AZ", "CO", "ID", "MT", "NM", "NV", "UT", "WY"],
    "Pacific":            ["AK", "CA", "HI", "OR", "WA"],
}
REGIONS["Northeast"] = REGIONS["New England"] + REGIONS["Mid-Atlantic"]
REGIONS["Midwest"]   = REGIONS["East North Central"] + REGIONS["West North Central"]
REGIONS["South"]     = REGIONS["South Atlantic"] + REGIONS["East South Central"] + REGIONS["West South Central"]
REGIONS["West"]      = REGIONS["Mountain"] + REGIONS["Pacific"]


# ──────────────────────────────────────────────────────────────────────────────
# Definitions the agent states verbatim (plan §4.2, §4.3 item 5)
# ──────────────────────────────────────────────────────────────────────────────
LOAD_FACTOR_BASIS = "seats"   # passengers / seats, BTS's own number — NOT RPM/ASM. Airport capacity
                              # is consumed per passenger, not per passenger-mile.

LONG_HAUL_DEFINITION = (
    "There is no standard definition of 'long haul'. This tool reports three proxies from airport-level "
    "BTS T-100 data: the international share of departures (counts freighters), the international share "
    "of available seat-miles (weights each flight by distance and seats; freighters contribute zero), and "
    "average stage length (domestic vs international). A share of flights above a mileage threshold is "
    "not computable from airport-level averages; it would need segment-level T-100."
)

# Up-gauging flag (plan §4.2 congestion; DESIGN.md limitations). Slots and gates are counted per
# FLIGHT, not per passenger, so an airline that cannot add flights adds seats per flight instead:
# departures flat, passengers up, passengers-per-departure up. That signature is more specific to
# an airport-side constraint than load factor alone (a fleet shortage shows up network-wide and
# washes out in within-tier z-scores; up-gauging that stands out from peers does not). Flagged in
# kpi.congestion(), never scored — 50-seat regional jets were retired industry-wide 2022–25, so
# the level of up-gauging is a trend; only the deviation from peers is a signal.
UPGAUGING_MAX_DEP_GROWTH = 0.02     # departures "flat": YoY growth at or below this
UPGAUGING_MIN_PAX_GROWTH = 0.03     # passengers "up": YoY growth at or above this

# direction: is the airport growing or shrinking, in words, next to every CPI. Pressure can come from
# demand outrunning supply (SFO) or from supply cut faster than demand (HNL, Apr 2026); CPI scores both,
# an investor must tell them apart. ±DIRECTION_BAND on pax_growth_yoy is "flat" — same noise band as
# RECOVERY_THRESHOLD (noise_check: median |YoY| across large hubs ≈ 2.6%).
DIRECTION_BAND = 0.02

# International seats per international departure below this ⇒ the departure count is dominated by
# all-cargo flights (a passenger widebody carries 250–350 seats; a freighter carries 0). Used only to
# flag, never to score. Plan §4.2 long-haul, §3.1.
CARGO_SEATS_PER_DEP_THRESHOLD = 60
# ...and only when there are enough international departures for the ratio to mean anything. BTV's
# few small regional flights to Canada tripped the flag without this.
CARGO_MIN_INTL_DEPARTURES = 300     # per trailing 12 months


# ──────────────────────────────────────────────────────────────────────────────
# KPIs — the per-airport Key Performance Indicators the agent reports and ranks on. One line each;
# this is the "KPIs" section of DESIGN.md and what explain_scoring returns first. CPI (above) is the
# ranking logic built on top of them. All are computed from BTS T-100 by scoring/metrics.py.
# ──────────────────────────────────────────────────────────────────────────────
KPIS = {
    "pax_ttm":             "Passengers enplaned in the trailing 12 months (size; used for tiers and the floor, never as merit)",
    "pax_growth_yoy":      "Passenger growth: trailing 12 months vs the 12 before (headline demand trend)",
    "pax_vs_2019":         "Passengers vs calendar 2019 — traffic relative to what the infrastructure was built for",
    "load_factor_ttm":     "Passengers / seats over the trailing 12 months, percent (seat-based, BTS's basis)",
    "lf_trend":            "Load factor now minus a year ago, percentage points",
    "seat_growth_yoy":     "Seat growth: are airlines adding supply?",
    "dep_growth_yoy":      "Departure growth: are airlines adding flights?",
    "pax_per_departure":   "Passengers per departure — average aircraft gauge",
    "gauge_growth_yoy":    "Change in passengers per departure — up-gauging when positive with flat departures",
    "intl_dep_share":      "International share of departures (includes all-cargo flights)",
    "intl_asm_share":      "International share of available seat-miles (freighters contribute zero)",
    "intl_pax_growth_yoy": "International passenger growth, trailing 12 months vs prior",
    "dom_pax_growth_yoy":  "Domestic passenger growth, trailing 12 months vs prior",
    "avg_stage_sm":        "Average stage length, statute miles (also split domestic / international)",
    "asm_ttm":             "Available seat-miles, trailing 12 months (industry capacity unit)",
    "rpm_ttm":             "Revenue passenger-miles, trailing 12 months (industry demand unit)",
    "seats_per_intl_dep":  "International seats per international departure — near zero means freighters dominate",
    "freight_lbs_ttm":     "Freight, pounds, trailing 12 months",
    "direction":           "expanding / flat / contracting — passenger growth above, within or below ±2% (which side the pressure comes from)",
}
