"""
Owner: Amit. Plan §4.4. Sanity checks against known airports, run with `pytest`.
They read data/airports.db (the committed snapshot) — no network in tests.

Assertions to write:
  - BOS load_factor for Apr 2026 ≈ 79.6 (matches BTS's published total_load_factor)
  - SNA pax_ttm < LAX pax_ttm
  - LAX hub_tier == "Large" and SNA hub_tier == "Medium"
  - rank_airports(region="New England") contains BOS and does not contain JFK
  - no NaN in cpi for airports above RANKING_FLOOR_PAX_TTM
  - recovered_2019 is False for at least one large hub and True for at least one
    (all-one-value means the threshold or the 2019 rows are wrong)
  - ANC seats_per_intl_dep is far below BOS's (confirms the freighter assumption behind the
    long-haul caveat; if it fails, that caveat changes)
"""
