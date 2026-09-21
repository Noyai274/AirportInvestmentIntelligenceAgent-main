"""
Owner: Amit. Plan §4.2, §4.3. Every tunable in ONE place; nothing else hard-codes a number.

Must define:
  WEIGHTS = {"growth": .2, "load_factor": .2, "lf_trend": .2, "demand_minus_supply": .2, "size": .2}
    - equal weights ARE the CPI definition (plan §4.2); change only for a reason you can name
  SENSITIVITY_SETS — the three alternative weight vectors for kpi.sensitivity(): equal,
    growth-heavy, utilisation-heavy
  RANKING_FLOOR_PAX_TTM — placeholder ~100_000; set empirically once the DB exists (plan §4.3 item 1)
  HUB_TIERS — FAA cut-offs on share of US TTM passengers: Large >= .01, Medium >= .0025,
    Small >= .0005, else Non-hub
  RECOVERY_THRESHOLD = -0.02   (plan §4.3 item 3; absolute, not sigma-based, on purpose)
  REGIONS = {"New England": ["CT","MA","ME","NH","RI","VT"], ...}
  TTM_ANCHOR = "latest"        (plan §4.3 item 2)
  LOAD_FACTOR_BASIS = "seats"  (plan §4.3 item 5 — not RPM/ASM)
  LONG_HAUL_DEFINITION — the one-line definition the long-haul tool states (plan §4.2)
"""
