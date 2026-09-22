"""
Owner: Claude. Plan §5.1; specified by question-bank.md (22 analyst questions).

The only functions the LLM can call. Each wraps scoring/ and ingest/cache and returns the same envelope:

    {"result": ..., "method": "<one line: how this was computed>",
     "caveats": [...], "source": "cache, fetched <date>" | "data.bts.gov live"}

The LLM never computes a number: every figure in an answer comes out of one of these functions.
TOOL_SCHEMAS at the bottom is what agent.py hands to the Anthropic API.
"""

import re
from datetime import datetime

import pandas as pd

from ingest.cache import get_monthly, get_monthly_all, refresh as cache_refresh, snapshot_stamp
from scoring import config
from scoring.kpi import _clean, congestion, decompose, definition, score
from scoring.kpi import sensitivity as kpi_sensitivity
from scoring.metrics import build

EXAM_AIRPORTS = ["BOS", "LAX", "SNA", "ANC", "SFO"]

# Columns a caller may sort or filter on. Anything else is rejected (the LLM is the caller).
RANKABLE = sorted(set(config.KPIS) | {"cpi", "rank_in_tier", "lf_trend", "intl_avg_stage_sm",
                                      "dom_avg_stage_sm", "months_ttm"})
FILTERABLE = sorted(set(RANKABLE) | {"hub_tier", "state", "recovered_2019", "upgauging", "direction",
                                     "cargo_dominated", "eligible"})
_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}

# Metro areas for resolve_airport: a city name that means several airports. First = default.
METROS = {
    "los angeles": ["LAX", "BUR", "LGB", "ONT", "SNA"], "la": ["LAX", "BUR", "LGB", "ONT", "SNA"],
    "new york": ["JFK", "LGA", "EWR"], "nyc": ["JFK", "LGA", "EWR"],
    "washington": ["DCA", "IAD", "BWI"], "dc": ["DCA", "IAD", "BWI"],
    "chicago": ["ORD", "MDW"], "san francisco": ["SFO", "OAK", "SJC"], "bay area": ["SFO", "OAK", "SJC"],
    "houston": ["IAH", "HOU"], "dallas": ["DFW", "DAL"], "miami": ["MIA", "FLL"],
    "south florida": ["MIA", "FLL", "PBI"], "boston": ["BOS"], "orange county": ["SNA"],
}

# ── shared caveat text (the agent is told to surface these) ───────────────────
CAVEAT_DATA = ("Source: BTS T-100 Segment Summary by Origin Airport (dataset r495-tyji) via the Socrata SODA API; "
               "US airports only; BTS publishes with a 4–5 month lag.")
CAVEAT_SEATS = ("'Seats' are aircraft seats offered by airlines, not terminal capacity. Capacity pressure on the "
                "airport itself is inferred from airline behaviour, not observed.")
CAVEAT_CARGO = "Departure counts include all-cargo flights; the passenger/cargo split is estimated from seats per departure."
CAVEAT_CPI = ("CPI is relative within hub tier (FAA classes by share of US passengers); equal weights are the "
              "definition, not an estimate. Airports below the ranking floor or with incomplete windows are not scored.")
CAVEAT_CONGESTION = "Congestion here is a capacity-utilisation proxy (load factor, demand vs seat growth, up-gauging), not a delay measure."
CAVEAT_DEMAND = "T-100 sees served demand only; suppressed demand (people who did not fly because of fares or missing service) is invisible."
CAVEAT_2019 = "2019 is a reference point (terminals were sized for it), seven years old and getting staler; it is a flag, not the headline."


# ── state: one scored table per baseline, rebuilt when the cache says the data changed ──────

_STATE: dict = {}


# _scored(baseline) -> (DataFrame, source)
def _scored(baseline: str = "yoy") -> tuple[pd.DataFrame, str]:
    stamp = snapshot_stamp()                       # one tiny query; the 80k-row read only happens on change
    if _STATE.get("stamp") != stamp:
        monthly, source = get_monthly_all()
        _STATE.clear()
        _STATE.update(stamp=snapshot_stamp(), source=source, monthly=monthly, metrics=build(monthly), frames={})
    if baseline not in _STATE["frames"]:
        sc = score(_STATE["metrics"], baseline=baseline)
        # tier_size = eligible airports in the tier — the denominator of rank_in_tier, carried on every row
        sc["tier_size"] = sc["hub_tier"].map(sc[sc["eligible"]].groupby("hub_tier").size()).fillna(0).astype(int)
        _STATE["frames"][baseline] = sc
    return _STATE["frames"][baseline], _STATE["source"]


def _invalidate() -> None:
    _STATE.clear()


# _envelope(result, method, caveats, source, notes=None) -> dict
#
# caveats     = facts about the result, for the user; the UI prints them, the agent relays them.
# agent_notes = instructions to the agent ("state the assumption", "offer the 2019 re-run"); never
#               printed, never quoted. Keeping the two apart stops directives leaking into answers.
def _envelope(result, method: str, caveats: list[str], source: str, notes: list[str] | None = None) -> dict:
    env = {"result": result, "method": method, "caveats": caveats, "source": source}
    if notes:
        env["agent_notes"] = notes
    return env


# _row(s, code, cols) -> dict of cleaned values
def _row(s: pd.DataFrame, code: str, cols: list[str]) -> dict:
    r = s.loc[code]
    return {"code": code, **{c: _clean(r.get(c)) for c in cols}}

_ROW_COLS = ["airport_name", "state", "hub_tier", "cpi", "rank_in_tier", "tier_size", "eligible", "pax_ttm",
             "pax_growth_yoy", "load_factor_ttm", "lf_trend", "seat_growth_yoy", "dep_growth_yoy",
             "pax_vs_2019", "recovered_2019", "direction", "upgauging", "cargo_dominated", "ttm_end"]


def _code(code: str) -> str:
    code = str(code).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3}", code):
        raise ValueError(f"not an airport code: {code!r} — use resolve_airport first")
    return code


def _known(s: pd.DataFrame, code: str) -> str:
    code = _code(code)
    if code not in s.index:
        raise ValueError(f"{code} is not in the data — use resolve_airport to find the right code")
    return code


# ══════════════════════════════ the eight tools ═══════════════════════════════

# rank_airports(region, state, tier, top_n, sort_by, baseline, filters, include_ineligible) -> envelope
def rank_airports(region: str | None = None, state: str | None = None, tier: str | None = None,
                  top_n: int = 10, sort_by: str = "cpi", baseline: str = "yoy",
                  filters: dict | None = None, include_ineligible: bool = False) -> dict:
    """Rank airports by CPI (default) or any KPI, optionally within a region / state / tier, with
    filters like {"load_factor_ttm": [">=", 85]}. Returns only scored (eligible) airports unless
    include_ineligible=True."""
    s, source = _scored(baseline)
    if sort_by not in RANKABLE:
        raise ValueError(f"sort_by must be one of {RANKABLE}")
    sub = s if include_ineligible else s[s["eligible"]]
    scope = []
    if region:
        if region not in config.REGIONS:
            raise ValueError(f"unknown region {region!r}; known: {list(config.REGIONS)}")
        sub = sub[sub["state"].isin(config.REGIONS[region])]; scope.append(f"region={region}")
    if state:
        sub = sub[sub["state"] == state.upper()]; scope.append(f"state={state.upper()}")
    if tier:
        sub = sub[sub["hub_tier"].str.lower() == tier.lower()]; scope.append(f"tier={tier}")
    for col, (op, val) in (filters or {}).items():
        if col not in FILTERABLE or op not in _OPS:
            raise ValueError(f"filter {col} {op}: column must be in {FILTERABLE}, op in {list(_OPS)}")
        sub = sub[_OPS[op](sub[col], val)]; scope.append(f"{col} {op} {val}")
    ascending = sort_by in ("rank_in_tier",)
    sub = sub.sort_values(sort_by, ascending=ascending).head(int(top_n))
    cols = _ROW_COLS + ([sort_by] if sort_by not in _ROW_COLS else [])
    rows = [_row(sub, c, cols) for c in sub.index]
    tiers = sorted(set(r["hub_tier"] for r in rows))
    caveats = [CAVEAT_CPI, CAVEAT_SEATS, CAVEAT_DEMAND]
    if len(tiers) > 1:
        caveats.insert(0, f"Results span hub tiers {tiers}: CPI is comparable within a tier, not across tiers — "
                          "read rank_in_tier and the raw KPIs when comparing across.")
    not_rec = [r["code"] for r in rows if r["recovered_2019"] is False]
    notes = []
    if not_rec:
        caveats.append(f"Still below 2019 traffic: {not_rec}.")
        notes.append("Recovery rule: name the airports still below 2019 and, once per conversation, offer the baseline='2019' re-run.")
    if len(tiers) > 1:
        notes.append("Present results grouped by hub tier, never as one list sorted by CPI across tiers.")
    method = (f"Sorted {'all' if include_ineligible else 'eligible'} airports by {sort_by} "
              f"(baseline={baseline}){' where ' + ', '.join(scope) if scope else ''}; top {top_n}. "
              f"TTM ending {s['ttm_end'].iloc[0]}.")
    return _envelope({"airports": rows, "count_in_scope": int(len(sub)), "sort_by": sort_by,
                      "baseline": baseline, "scope": scope}, method, caveats, source, notes)


# airport_profile(code, refresh, baseline) -> envelope
def airport_profile(code: str, refresh: bool = False, baseline: str = "yoy") -> dict:
    """Every KPI plus CPI, tier, rank-in-tier and the tier's top 3 for one airport.
    refresh=True bypasses the cache and pulls this airport live from BTS."""
    refresh_note = None
    if refresh:
        try:
            cache_refresh(_code(code)); _invalidate()
        except Exception as exc:                       # BTS down / no network: answer from cache, say so
            refresh_note = f"live refresh failed ({type(exc).__name__}); answering from cache"
    s, source = _scored(baseline)
    code = _known(s, code)
    r = s.loc[code]
    kpis = {k: _clean(r.get(k)) for k in config.KPIS}
    tier_peers = s[(s["hub_tier"] == r["hub_tier"]) & s["eligible"]].sort_values("cpi", ascending=False)
    top3 = [{"code": c, "cpi": _clean(tier_peers.loc[c, "cpi"])} for c in tier_peers.index[:3]]
    result = {
        "identity": {"code": code, "airport_name": r["airport_name"], "city": r["city"], "state": r["state"]},
        "hub_tier": r["hub_tier"], "tier_size": int(r["tier_size"]),
        "eligible": bool(r["eligible"]), "cpi": _clean(r["cpi"]), "rank_in_tier": _clean(r["rank_in_tier"]),
        "tier_top3": top3, "recovered_2019": bool(r["recovered_2019"]), "upgauging": bool(r["upgauging"]),
        "cargo_dominated": bool(r["cargo_dominated"]), "ttm_end": r["ttm_end"], "baseline": baseline,
        "kpis": kpis, "months": {"ttm": int(r["months_ttm"]), "prior": int(r["months_prior"]), "2019": int(r["months_2019"])},
    }
    if refresh and not refresh_note:
        source = "data.bts.gov live (refreshed on request)"
    caveats = [CAVEAT_DATA, CAVEAT_SEATS, CAVEAT_CPI]
    if refresh_note:
        caveats.insert(0, refresh_note)
    if not r["eligible"]:
        caveats.append(f"{code} is not scored (below the {config.RANKING_FLOOR_PAX_TTM:,} passenger floor or incomplete windows); KPIs are still reported.")
    if r["cargo_dominated"]:
        caveats.append(CAVEAT_CARGO)
    return _envelope(result, f"All KPIs from scoring.metrics.build; CPI and rank from scoring.kpi.score (baseline={baseline}).", caveats, source)


# compare_airports(codes, baseline) -> envelope
def compare_airports(codes: list[str], baseline: str = "yoy") -> dict:
    """Side-by-side KPIs, CPI and congestion proxy for 2–6 airports; flags cross-tier comparisons."""
    s, source = _scored(baseline)
    codes = [_known(s, c) for c in codes]
    if not 2 <= len(codes) <= 6:
        raise ValueError("compare 2 to 6 airports")
    rows = {c: _row(s, c, _ROW_COLS + ["pax_per_departure", "gauge_growth_yoy", "intl_asm_share"]) for c in codes}
    cong = {c: congestion(s, c) for c in codes}
    tiers = sorted(set(rows[c]["hub_tier"] for c in codes))
    caveats = [CAVEAT_CONGESTION, CAVEAT_SEATS, CAVEAT_CPI]
    if len(tiers) > 1:
        pairs = ", ".join(f"{c}={rows[c]['hub_tier']}" for c in codes)
        caveats.insert(0, f"Cross-tier comparison ({pairs}): CPI and z-scores are relative to each airport's own tier. "
                          "Compare raw KPIs directly; compare CPI only as 'standing within its class'.")
    not_rec = [c for c in codes if rows[c]["recovered_2019"] is False]
    notes = []
    if not_rec:
        caveats.append(f"Still below 2019 traffic: {not_rec}.")
        notes.append("Recovery rule: name the airports still below 2019 and, once per conversation, offer the baseline='2019' re-run.")
    known = {"SNA": "SNA operates under a court-settlement cap on annual passengers and flights and a night curfew — a regulatory constraint outside the data.",
             "ANC": "ANC is a major cargo hub (Asia–North America freighter refuelling); its departure counts are freighter-heavy.",
             "LGA": "LGA is slot-controlled and perimeter-restricted by regulation — outside the data.",
             "DCA": "DCA is slot-controlled and perimeter-restricted by regulation — outside the data.",
             "JFK": "JFK is slot-controlled by regulation — outside the data.",
             "EWR": "EWR operates under an FAA order limiting hourly operations (2025–26): departure counts are constrained by regulation, not demand — outside the data."}
    caveats += [known[c] for c in codes if c in known]
    return _envelope({"airports": rows, "congestion": cong, "tiers": tiers, "baseline": baseline},
                     f"KPIs and CPI side by side for {codes} (baseline={baseline}); congestion proxy from scoring.kpi.congestion.",
                     caveats, source, notes)


# long_haul_share(code) -> envelope
def long_haul_share(code: str) -> dict:
    """Three long-haul proxies for one airport, the definition used, and what cannot be computed."""
    s, source = _scored()
    code = _known(s, code)
    r = s.loc[code]
    bos = s.loc["BOS"] if "BOS" in s.index else None
    result = {
        "code": code, "ttm_end": r["ttm_end"],
        "intl_share_of_departures": _clean(r["intl_dep_share"]),
        "intl_share_of_seat_miles": _clean(r["intl_asm_share"]),
        "avg_stage_sm": {"all": _clean(r["avg_stage_sm"]), "domestic": _clean(r["dom_avg_stage_sm"]), "international": _clean(r["intl_avg_stage_sm"])},
        "seats_per_intl_departure": _clean(r["seats_per_intl_dep"]),
        "seats_per_intl_departure_BOS_reference": _clean(bos["seats_per_intl_dep"]) if bos is not None else None,
        "cargo_dominated": bool(r["cargo_dominated"]),
        "freight_lbs_ttm": _clean(r["freight_lbs_ttm"]),
        "intl_pax_growth_yoy": _clean(r["intl_pax_growth_yoy"]), "dom_pax_growth_yoy": _clean(r["dom_pax_growth_yoy"]),
        "definition": config.LONG_HAUL_DEFINITION,
        "not_computable": "share of flights above a mileage threshold — needs segment-level T-100 (origin–destination rows), not airport-level averages",
    }
    caveats = [CAVEAT_CARGO, CAVEAT_DATA]
    if r["cargo_dominated"]:
        caveats.insert(0, f"{code}'s international departures average {result['seats_per_intl_departure']:.0f} seats vs "
                          f"{result['seats_per_intl_departure_BOS_reference']:.0f} at BOS — freighters dominate the count. "
                          "Ask whether the user means all flights or passenger flights; report both shares.")
    return _envelope(result, "International departures / all departures; international ASM / all ASM; departure-weighted average stage lengths; all trailing 12 months.", caveats, source)


# unmet_demand(code, baseline) -> envelope
def unmet_demand(code: str, baseline: str = "yoy") -> dict:
    """Decompose one airport's CPI into components (biggest driver first), with the congestion proxy
    and the pre-COVID position — the 'why' tool."""
    s, source = _scored(baseline)
    code = _known(s, code)
    d = decompose(s, code); c = congestion(s, code)
    demand_terms = [p for p in d["components"] if p["component"] in ("load_factor", "lf_trend", "demand_minus_supply")]
    result = {**d, "unmet_demand_subscore": _clean(sum((p["contribution"] or 0) for p in demand_terms)),
              "congestion": c}
    caveats = [CAVEAT_DEMAND, CAVEAT_SEATS, CAVEAT_2019, CAVEAT_CPI]
    notes = []
    if not d["recovered_2019"]:
        caveats.insert(0, f"{code} is {d['pax_vs_2019']:+.1%} vs 2019: still below the traffic its terminals were sized for.")
        notes.append("This is part of the 'why'. Recovery rule: offer the baseline='2019' re-run once per conversation.")
    return _envelope(result, "CPI decomposition (weight × within-tier z per component) from scoring.kpi.decompose; unmet-demand sub-score = load_factor + lf_trend + demand_minus_supply contributions.", caveats, source, notes)


# explain_scoring(sensitivity) -> envelope
def explain_scoring(sensitivity: bool = False) -> dict:
    """The KPIs, then the ranking logic (CPI): formula, weights, tiers, floor, thresholds. With
    sensitivity=True, the rank of the exam airports under three weight definitions."""
    s, source = _scored()
    result = {"kpis": config.KPIS, "ranking_logic": definition(),
              "data": {"dataset": "r495-tyji", "ttm_end": s["ttm_end"].iloc[0], "airports_in_data": int(len(s)),
                       "airports_scored": int(s["eligible"].sum()),
                       "tier_counts": {k: int(v) for k, v in s[s["eligible"]]["hub_tier"].value_counts().items()}}}
    if sensitivity:
        codes = [c for c in EXAM_AIRPORTS if c in s.index and s.loc[c, "eligible"]]
        ne = s[s["eligible"] & s["state"].isin(config.REGIONS["New England"])].sort_values("cpi", ascending=False).index[:3].tolist()
        tbl = kpi_sensitivity(_STATE["metrics"], codes + [c for c in ne if c not in codes])
        result["sensitivity"] = {"weight_sets": config.SENSITIVITY_SETS,
                                 "rank_in_tier": {c: {k: _clean(v) for k, v in tbl.loc[c].items()} for c in tbl.index}}
    return _envelope(result, "Definitions read from scoring.config; sensitivity from scoring.kpi.sensitivity.",
                     [CAVEAT_CPI, CAVEAT_SEATS, "Weights are a stated definition; the sensitivity table shows how much the ranking depends on them, not that they are 'right'."], source)


# resolve_airport(query) -> envelope
def resolve_airport(query: str) -> dict:
    """Turn 'LA', 'Santa Ana', 'Logan', 'JFK' into airport codes. Returns a best match plus
    alternatives; the agent states the assumption when several airports fit."""
    s, source = _scored()
    q = query.strip().lower()
    if q.upper() in s.index and re.fullmatch(r"[a-z0-9]{3}", q):
        return _envelope({"best": q.upper(), "alternatives": [], "ambiguous": False}, "exact code match", [], source)
    if q in METROS:
        cands = [c for c in METROS[q] if c in s.index]
        return _envelope({"best": cands[0], "alternatives": cands[1:], "ambiguous": len(cands) > 1,
                          "note": f"'{query}' is a metro area with {len(cands)} airports; defaulting to {cands[0]}"},
                         "metro-area table", [f"'{query}' is ambiguous: {cands[0]} assumed; alternatives {cands[1:]}."],
                         source, [f"In the answer, say you read '{query}' as {cands[0]} and list the alternatives {cands[1:]}; proceed without asking."])
    names = (s["airport_name"].fillna("") + " " + s["city"].fillna("")).str.lower()
    hits = s[names.str.contains(re.escape(q), regex=True)]
    hits = hits.sort_values("pax_ttm", ascending=False)
    cands = hits.index.tolist()[:6]
    if not cands:
        return _envelope({"best": None, "alternatives": [], "ambiguous": False, "note": f"no airport matches '{query}'"},
                         "substring match on airport and city names", [f"No airport matches '{query}'."], source,
                         ["Ask the user for the airport code or city."])
    return _envelope({"best": cands[0], "alternatives": cands[1:], "ambiguous": len(cands) > 1,
                      "matches": [{"code": c, "airport_name": s.loc[c, "airport_name"], "state": s.loc[c, "state"], "pax_ttm": _clean(s.loc[c, "pax_ttm"])} for c in cands]},
                     "substring match on airport and city names, largest first", [], source)


# trend(code, metric, months) -> envelope
def trend(code: str, metric: str = "passengers", months: int = 36) -> dict:
    """Monthly time series for one airport — the only tool that returns months, not one number."""
    code = _code(code)
    allowed = ["passengers", "seats", "departures", "load_factor", "intl_passengers", "dom_passengers", "freight_lbs", "intl_departures"]
    if metric not in allowed:
        raise ValueError(f"metric must be one of {allowed}")
    df, source = get_monthly(code)
    df = df.sort_values("month").tail(int(months))
    series = [{"month": m[:7], metric: _clean(v)} for m, v in zip(df["month"], df[metric])]
    return _envelope({"code": code, "metric": metric, "months": len(series), "series": series},
                     f"Monthly {metric} for {code}, last {len(series)} months, straight from the cached T-100 rows.",
                     [CAVEAT_DATA, "Monthly values are seasonal; compare a month to the same month a year earlier."], source)


TOOLS = {f.__name__: f for f in [rank_airports, airport_profile, compare_airports, long_haul_share,
                                  unmet_demand, explain_scoring, resolve_airport, trend]}


# ══════════════════════ JSON schemas for the Anthropic API ══════════════════════
_baseline = {"type": "string", "enum": ["yoy", "2019"], "description": "growth basis: yoy (default) or 2019 (pre-COVID reference, on request)"}
TOOL_SCHEMAS = [
    {"name": "rank_airports",
     "description": "Rank US airports by Capacity Pressure Index (default) or any KPI, within a region/state/hub tier, with optional filters. Use for 'which airports…' questions.",
     "input_schema": {"type": "object", "properties": {
         "region": {"type": "string", "description": f"one of {list(config.REGIONS)}"},
         "state": {"type": "string", "description": "two-letter US state"},
         "tier": {"type": "string", "enum": ["Large", "Medium", "Small", "Non-hub"]},
         "top_n": {"type": "integer", "default": 10},
         "sort_by": {"type": "string", "description": f"one of {RANKABLE}", "default": "cpi"},
         "baseline": _baseline,
         "filters": {"type": "object", "description": 'e.g. {"load_factor_ttm": [">=", 85], "recovered_2019": ["==", false]}',
                     "additionalProperties": {"type": "array", "minItems": 2, "maxItems": 2}},
         "include_ineligible": {"type": "boolean", "default": False}}}},
    {"name": "airport_profile",
     "description": "All KPIs, CPI, tier and rank for one airport. refresh=true pulls it live from BTS.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "refresh": {"type": "boolean", "default": False}, "baseline": _baseline}, "required": ["code"]}},
    {"name": "compare_airports",
     "description": "Side-by-side KPIs, CPI and congestion proxy for 2–6 airports. Use for 'compare A and B' questions.",
     "input_schema": {"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6}, "baseline": _baseline}, "required": ["codes"]}},
    {"name": "long_haul_share",
     "description": "Long-haul / international proxies for one airport: share of departures, share of seat-miles, stage lengths, cargo flag.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "unmet_demand",
     "description": "Why an airport scores as it does: CPI decomposition, unmet-demand sub-score, congestion proxy, pre-COVID position.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "baseline": _baseline}, "required": ["code"]}},
    {"name": "explain_scoring",
     "description": "The KPIs and the ranking logic (CPI): formula, weights, tiers, thresholds; with sensitivity=true, the rank table under alternative weights.",
     "input_schema": {"type": "object", "properties": {"sensitivity": {"type": "boolean", "default": False}}}},
    {"name": "resolve_airport",
     "description": "Turn a city, airport name or code ('LA', 'Santa Ana', 'Logan') into airport codes with alternatives. Call this before other tools when the user did not give a 3-letter code.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "trend",
     "description": "Monthly time series of one metric for one airport (passengers, seats, departures, load_factor, …).",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "metric": {"type": "string", "default": "passengers"}, "months": {"type": "integer", "default": 36}}, "required": ["code"]}},
]
