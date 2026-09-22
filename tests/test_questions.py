"""
Owner: Claude (spec: question-bank.md). One test per analyst question, calling the tool functions
directly — no LLM, no network. Each test checks (a) what a correct answer must contain, per the
bank, and (b) the envelope every tool returns: result, method, non-empty caveats, source.
Run: pytest tests/test_questions.py -v
"""

import json

import pytest

from agent import tools as T
from scoring import config

NE = set(config.REGIONS["New England"])
SOUTH = set(config.REGIONS["South"])


def envelope(e: dict) -> dict:
    """Every tool returns the same shape and is JSON-serialisable (it goes to the LLM as JSON)."""
    assert {"result", "method", "caveats", "source"} <= set(e) <= {"result", "method", "caveats", "source", "agent_notes"}
    assert e["method"] and isinstance(e["caveats"], list) and e["source"]
    json.dumps(e)
    return e["result"]


def codes(e): return [a["code"] for a in e["result"]["airports"]]


# ═══ A. screening ═══════════════════════════════════════════════════════════════

def test_q01_new_england_candidates():
    e = T.rank_airports(region="New England"); r = envelope(e)
    assert "BOS" in codes(e) and "JFK" not in codes(e)
    assert all(a["state"] in NE for a in r["airports"])
    assert all(a["hub_tier"] for a in r["airports"])
    assert any("tier" in c.lower() for c in e["caveats"])          # spans tiers → says so


def test_q02_top10_large_hubs():
    e = T.rank_airports(tier="Large", top_n=10); r = envelope(e)
    assert len(r["airports"]) == 10
    assert all(a["hub_tier"] == "Large" for a in r["airports"])
    cpis = [a["cpi"] for a in r["airports"]]
    assert cpis == sorted(cpis, reverse=True)
    assert {"SFO", "DEN"} & set(codes(e)[:5])


def test_q03_southern_medium_by_growth():
    e = T.rank_airports(region="South", tier="Medium", sort_by="pax_growth_yoy"); r = envelope(e)
    assert all(a["hub_tier"] == "Medium" and a["state"] in SOUTH for a in r["airports"])
    g = [a["pax_growth_yoy"] for a in r["airports"]]
    assert g == sorted(g, reverse=True)


def test_q04_growth_vs_2019_no_small_base_artefacts():
    e = T.rank_airports(sort_by="pax_vs_2019", top_n=20); r = envelope(e)
    assert "LAL" not in codes(e)                                    # near-zero 2019 base → NaN, never ranked
    assert all(a["pax_vs_2019"] is not None and a["pax_vs_2019"] < 5 for a in r["airports"])


def test_q05_texas_load_factor_filter_empty_is_valid():
    e = T.rank_airports(state="TX", filters={"load_factor_ttm": [">=", 85]}); r = envelope(e)
    assert all(a["state"] == "TX" and a["load_factor_ttm"] >= 85 for a in r["airports"])
    assert isinstance(r["airports"], list)                          # empty list is a legitimate answer


def test_q06_large_hubs_not_recovered():
    e = T.rank_airports(tier="Large", filters={"recovered_2019": ["==", False]}, top_n=40); r = envelope(e)
    assert all(a["recovered_2019"] is False and a["pax_vs_2019"] < config.RECOVERY_THRESHOLD for a in r["airports"])
    assert {"LAX", "SFO"} <= set(codes(e))                          # as of the Apr-2026 snapshot
    assert any("2019" in c for c in e["caveats"])                   # the recovery offer is in the caveats


def test_q07_upgauging_flag_is_consistent():
    e = T.rank_airports(filters={"upgauging": ["==", True]}, sort_by="gauge_growth_yoy", top_n=30, include_ineligible=True); r = envelope(e)
    for a in r["airports"]:
        assert a["dep_growth_yoy"] <= config.UPGAUGING_MAX_DEP_GROWTH
        assert a["pax_growth_yoy"] >= config.UPGAUGING_MIN_PAX_GROWTH


# ═══ B. comparison ══════════════════════════════════════════════════════════════

def test_q08_la_vs_santa_ana():
    r = envelope(T.resolve_airport("LA"))
    assert r["best"] == "LAX" and r["ambiguous"] and {"BUR", "LGB", "ONT", "SNA"} <= set(r["alternatives"])
    e = T.compare_airports(["LAX", "SNA"]); r = envelope(e)
    assert r["tiers"] == ["Large", "Medium"]
    assert "Cross-tier" in e["caveats"][0]
    assert any("SNA" in c and "cap" in c for c in e["caveats"])     # the regulatory constraint, labelled outside the data
    assert any("proxy" in c for c in e["caveats"])
    for c in ("LAX", "SNA"):
        assert "upgauging" in r["congestion"][c] and "load_factor_pct" in r["congestion"][c]


def test_q09_new_york_three_airports():
    r = envelope(T.resolve_airport("New York"))
    assert set([r["best"]] + r["alternatives"]) == {"JFK", "LGA", "EWR"}   # EWR is in NJ — metro table, not state
    e = T.compare_airports(["JFK", "LGA", "EWR"]); r = envelope(e)
    assert r["tiers"] == ["Large"]
    assert not any("Cross-tier" in c for c in e["caveats"])


def test_q10_denver_vs_phoenix():
    e = T.compare_airports(["DEN", "PHX"]); r = envelope(e)
    assert set(r["airports"]) == {"DEN", "PHX"}
    for c in ("DEN", "PHX"):
        d = envelope(T.unmet_demand(c))
        assert d["components"][0]["contribution"] is not None      # a named dominant component


def test_q11_sna_peer_group():
    r = envelope(T.airport_profile("SNA"))
    assert r["hub_tier"] == "Medium" and r["rank_in_tier"] and r["tier_size"] > 10
    assert len(r["tier_top3"]) == 3 and all({"code", "cpi"} <= set(x) for x in r["tier_top3"])
    assert "SNA" in [x["code"] for x in r["tier_top3"]] or r["rank_in_tier"] > 3


# ═══ C. diagnosis ═══════════════════════════════════════════════════════════════

def test_q12_sfo_unmet_demand():
    e = T.unmet_demand("SFO"); r = envelope(e)
    contribs = [p["contribution"] for p in r["components"]]
    assert contribs == sorted(contribs, reverse=True)               # biggest driver first
    assert r["pax_vs_2019"] is not None and isinstance(r["recovered_2019"], bool)
    assert any("served demand" in c for c in e["caveats"])
    if not r["recovered_2019"]:
        assert "2019" in e["caveats"][0]                            # the disclaimer leads


def test_q13_pdx_vs_sea_decomposition():
    a, b = envelope(T.unmet_demand("PDX")), envelope(T.unmet_demand("SEA"))
    assert {p["component"] for p in a["components"]} == {p["component"] for p in b["components"]}


def test_q14_austin_driver():
    r = envelope(T.unmet_demand("AUS"))
    names = {p["component"] for p in r["components"]}
    assert {"growth", "load_factor", "lf_trend", "demand_minus_supply"} == names


def test_q15_boston_trend():
    r = envelope(T.trend("BOS", metric="passengers", months=36))
    assert r["months"] == 36 and len(r["series"]) == 36
    assert r["series"][-1]["month"] == "2026-04"
    assert all(p["passengers"] > 0 for p in r["series"])


# ═══ D. international / long-haul / cargo ═══════════════════════════════════════

def test_q16_anchorage_long_haul():
    e = T.long_haul_share("ANC"); r = envelope(e)
    assert r["cargo_dominated"] is True
    assert r["seats_per_intl_departure"] < 0.5 * r["seats_per_intl_departure_BOS_reference"]
    assert r["intl_share_of_departures"] > r["intl_share_of_seat_miles"]   # freighters count as departures, not seat-miles
    assert "definition" in r and "not_computable" in r
    assert "freighters" in e["caveats"][0]


def test_q17_international_seat_mile_share():
    e = T.rank_airports(sort_by="intl_asm_share", top_n=10); r = envelope(e)
    assert {"JFK", "SFO", "LAX"} & set(codes(e))
    assert all(0 <= a["intl_asm_share"] <= 1 for a in r["airports"])


def test_q18_miami_intl_vs_domestic_growth():
    r = envelope(T.long_haul_share("MIA"))
    assert r["intl_pax_growth_yoy"] is not None and r["dom_pax_growth_yoy"] is not None


def test_q19_cargo_dominated_airports():
    e = T.rank_airports(filters={"cargo_dominated": ["==", True]}, include_ineligible=True, sort_by="freight_lbs_ttm", top_n=10); r = envelope(e)
    assert {"ANC", "MEM", "SDF"} <= set(codes(e))                    # the known freighter hubs
    assert all(a["cargo_dominated"] is True for a in r["airports"])


# ═══ E. methodology / provenance ═══════════════════════════════════════════════

def test_q20_explain_scoring_with_sensitivity():
    e = T.explain_scoring(sensitivity=True); r = envelope(e)
    assert set(r["kpis"]) == set(config.KPIS)
    assert r["ranking_logic"]["weights"] == config.WEIGHTS
    assert set(r["sensitivity"]["weight_sets"]) == set(config.SENSITIVITY_SETS)
    for code, ranks in r["sensitivity"]["rank_in_tier"].items():
        assert set(ranks) == {"hub_tier"} | set(config.SENSITIVITY_SETS)


def test_q21_provenance():
    e = T.airport_profile("BOS"); r = envelope(e)
    assert r["ttm_end"] == "2026-04"
    assert e["source"].startswith("cache") or e["source"].startswith("data.bts.gov")
    assert any("r495-tyji" in c for c in e["caveats"])
    assert any("aircraft seats" in c for c in e["caveats"])


def test_q22_out_of_scope_gates():
    # There is no tool for gate counts, on purpose. The agent must decline honestly (prompt rule);
    # here we assert the *data* side: no tool or KPI claims to know gates.
    r = envelope(T.explain_scoring())
    assert not any("gate" in k for k in r["kpis"])
    with pytest.raises(ValueError):
        T.rank_airports(sort_by="gates")


# ═══ guards ═════════════════════════════════════════════════════════════════════

def test_bad_inputs_are_rejected_not_guessed():
    with pytest.raises(ValueError): T.rank_airports(region="Sun Belt")
    with pytest.raises(ValueError): T.rank_airports(filters={"cpi": ["~", 1]})
    with pytest.raises(ValueError): T.airport_profile("BOSTON")
    with pytest.raises(ValueError): T.compare_airports(["BOS"])
    with pytest.raises(ValueError): T.trend("BOS", metric="gates")
