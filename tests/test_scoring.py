"""
Owner: Amit. Plan §4.4. Sanity checks against known airports, run with `pytest`.

Two sections:
  A. Data assumptions — run today, against data/airports.db. They test the ingest layer and the
     assumptions the plan makes about BTS data. No network: the cache is read with a huge
     max_age_days so it never refetches. If the DB is empty, the tests skip with a message.
  B. Scoring — placeholders that skip until scoring/metrics.py and scoring/kpi.py exist.
"""

import pytest

from ingest.cache import get_monthly_all, _validate_code

NEVER_REFETCH = 10**6          # days; "use whatever is cached, never go to BTS" — tests must be offline


# ──────────────────────────────────────────────────────────────────────────────
# monthly (fixture) -> DataFrame
#
# Loads the whole cached table once per test session and hands it to every test. If the cache is
# empty (build_db has not been run) the whole module skips rather than failing, with the command
# to run. `scope="module"` means one read for all tests, not one per test.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def monthly():
    df, source = get_monthly_all(max_age_days=NEVER_REFETCH)
    if df.empty:
        pytest.skip("data/airports.db is empty — run `python -m ingest.build_db` first")
    assert source.startswith("cache"), "tests must never hit the network"
    return df


# ──────────────────────────────────────────────────────────────────────────────
# latest_year(monthly) -> DataFrame
#
# The rows of the latest calendar year in the data (e.g. all 2026 months). Used by the tests
# that need a recent window rather than the full history.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def latest_year(monthly):
    year = monthly["month"].max()[:4]
    return monthly[monthly["month"].str.startswith(year)]


# ═══════════════════════════ A. data assumptions ═══════════════════════════════

# ──────────────────────────────────────────────────────────────────────────────
# test_bos_load_factor_matches_bts
#
# The one number we verified by hand on data.bts.gov: BOS, April 2026, total_load_factor 79.6.
# If this fails, either the rename map points `load_factor` at the wrong BTS field, or the
# text-to-number conversion in _to_frame went wrong. Tolerance 0.05 because BTS stores one decimal.
# ──────────────────────────────────────────────────────────────────────────────
def test_bos_load_factor_matches_bts(monthly):
    row = monthly[(monthly["code"] == "BOS") & (monthly["month"] == "2026-04-01")]
    assert len(row) == 1, "expected exactly one BOS row for 2026-04"
    assert abs(row["load_factor"].iloc[0] - 79.6) < 0.05


# ──────────────────────────────────────────────────────────────────────────────
# test_full_history_per_airport
#
# Jan 2019 → the latest month should be one row per month for a large hub. Catches silent
# pagination loss (the range bug in soda.py would have shown up here) and a broken `since` filter.
# ──────────────────────────────────────────────────────────────────────────────
def test_full_history_per_airport(monthly):
    months = monthly["month"].nunique()
    for code in ["BOS", "LAX", "SFO", "ANC"]:
        assert (monthly["code"] == code).sum() == months, f"{code} is missing months"


# ──────────────────────────────────────────────────────────────────────────────
# test_sna_smaller_than_lax
#
# Sanity on magnitudes: Santa Ana is a medium hub, LAX is one of the largest airports in the
# world. If SNA's passengers exceed LAX's, columns are swapped somewhere.
# ──────────────────────────────────────────────────────────────────────────────
def test_sna_smaller_than_lax(latest_year):
    pax = latest_year.groupby("code")["passengers"].sum()
    assert pax["SNA"] < pax["LAX"]


# ──────────────────────────────────────────────────────────────────────────────
# test_state_lookup_joined
#
# The state column comes from airport_states.csv joined at read time (cache._with_state).
# BOS → MA and JFK → NY prove the join works and the OurAirports parsing put the right two
# letters in. If `state` is NaN for BOS, the CSV is missing or has the wrong columns.
# ──────────────────────────────────────────────────────────────────────────────
def test_state_lookup_joined(monthly):
    state = monthly.drop_duplicates("code").set_index("code")["state"]
    assert state["BOS"] == "MA"
    assert state["JFK"] == "NY"


# ──────────────────────────────────────────────────────────────────────────────
# test_freighters_are_in_departure_counts   ← the plan §3.1 assumption
#
# Assumption: T-100 counts all-cargo flights as departures. If true, ANC — a refuelling stop for
# Asia–North America freighters — has many international departures with ZERO seats, so its
# "international seats per international departure" collapses, while BOS (a passenger airport)
# sits near a widebody's 200–300 seats. We require ANC to be below half of BOS.
#
# If this test FAILS the assumption is wrong, and three things change (plan §4.2, §9):
#   the cargo caveat in the long-haul answer is dropped, `seats_per_intl_dep` leaves the metrics
#   table, and this test is deleted. That is a finding, not a bug — do not "fix" the threshold.
# ──────────────────────────────────────────────────────────────────────────────
def test_freighters_are_in_departure_counts(latest_year):
    def seats_per_intl_dep(code: str) -> float:
        s = latest_year[latest_year["code"] == code]
        return s["intl_seats"].sum() / max(s["intl_departures"].sum(), 1)
    anc, bos = seats_per_intl_dep("ANC"), seats_per_intl_dep("BOS")
    assert bos > 100, f"BOS intl seats/departure = {bos:.0f}; expected a passenger-aircraft number"
    assert anc < 0.5 * bos, f"ANC={anc:.0f} vs BOS={bos:.0f}: freighter assumption NOT confirmed"


# ──────────────────────────────────────────────────────────────────────────────
# test_validate_code_guard
#
# The boundary between the LLM's tool arguments and our SoQL string. Lower-case is normalised,
# anything that is not three letters/digits is rejected. Pure function, no data needed.
# ──────────────────────────────────────────────────────────────────────────────
def test_validate_code_guard():
    assert _validate_code(" bos ") == "BOS"
    for bad in ["BOSTON", "BO", "", "B'S", "LAX; DROP TABLE monthly"]:
        with pytest.raises(ValueError):
            _validate_code(bad)


# ═══════════════════════════════ B. scoring ═════════════════════════════════════
# These run once scoring/metrics.py and scoring/kpi.py exist; until then they skip.

# ──────────────────────────────────────────────────────────────────────────────
# metrics (fixture) -> DataFrame indexed by airport code
#
# Builds the per-airport metrics table from the cached monthly rows via scoring.metrics.build.
# importorskip makes the whole section skip cleanly while that module is not written yet.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def metrics(monthly):
    m = pytest.importorskip("scoring.metrics")
    return m.build(monthly)


# ──────────────────────────────────────────────────────────────────────────────
# test_hub_tiers
#
# LAX is a Large hub (≥1% of US passengers), SNA is Medium (0.25–1%). If either is wrong, the
# share computation or the FAA cut-offs in config are off.
# ──────────────────────────────────────────────────────────────────────────────
def test_hub_tiers(metrics):
    assert metrics.loc["LAX", "hub_tier"] == "Large"
    assert metrics.loc["SNA", "hub_tier"] == "Medium"


# ──────────────────────────────────────────────────────────────────────────────
# test_recovery_flag_is_not_degenerate
#
# recovered_2019 must be False for at least one large hub and True for at least one. If every
# airport has the same value, either RECOVERY_THRESHOLD or the 2019 rows are wrong.
# ──────────────────────────────────────────────────────────────────────────────
def test_recovery_flag_is_not_degenerate(metrics):
    large = metrics[metrics["hub_tier"] == "Large"]["recovered_2019"]
    assert large.any() and not large.all()


# ──────────────────────────────────────────────────────────────────────────────
# test_cpi_has_no_nan_above_floor
#
# score() returns EVERY airport on purpose — ineligible ones (below the floor, incomplete windows)
# come back with eligible=False and cpi=NaN so a profile question still gets raw metrics. So the
# assertion is on the eligible set only: every scored airport has a CPI, and the eligible set is
# not absurdly small (a floor typo — 100_000_000 — would pass an empty check).
# ──────────────────────────────────────────────────────────────────────────────
def test_cpi_has_no_nan_above_floor(metrics):
    kpi = pytest.importorskip("scoring.kpi")
    scored = kpi.score(metrics)
    eligible = scored[scored["eligible"]]
    assert len(eligible) > 100, f"only {len(eligible)} airports eligible — check RANKING_FLOOR_PAX_TTM"
    assert eligible["cpi"].notna().all()
    assert scored.loc[~scored["eligible"], "cpi"].isna().all()   # and ineligible ones are NaN, not 0


# ──────────────────────────────────────────────────────────────────────────────
# test_new_england_membership
#
# The region question: BOS is in, JFK is out. Tests the REGIONS dict in config and the state join.
# ──────────────────────────────────────────────────────────────────────────────
def test_new_england_membership(metrics):
    cfg = pytest.importorskip("scoring.config")
    ne = metrics[metrics["state"].isin(cfg.REGIONS["New England"])].index
    assert "BOS" in ne and "JFK" not in ne


# ──────────────────────────────────────────────────────────────────────────────
# test_size_is_not_merit
#
# The post-mortem test (2026-09-22). Within every tier, the biggest airport must not be
# systematically ranked higher: the correlation between pax_ttm and CPI inside a tier should be
# weak, and an airport whose four raw components are all below its tier's median cannot sit in the
# top quarter of that tier. Before `size` was removed, LAX (shrinking) ranked 12/30 on size alone.
# ──────────────────────────────────────────────────────────────────────────────
def test_size_is_not_merit(metrics):
    from scoring import kpi
    scored = kpi.score(metrics)
    large = scored[scored["eligible"] & (scored["hub_tier"] == "Large")]
    assert abs(large["pax_ttm"].corr(large["cpi"])) < 0.5
    raw = large[[f"raw_{c}" for c in kpi.COMPONENTS]]
    all_weak = (raw < raw.median()).all(axis=1)
    top_quarter = large["rank_in_tier"] <= len(large) / 4
    assert not (all_weak & top_quarter).any()
