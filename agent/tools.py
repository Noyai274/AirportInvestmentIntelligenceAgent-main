"""
Owner: Claude. Plan §5.1. The only functions the LLM can call. Each wraps scoring/ and ingest/cache.

Every tool returns the same shape:
  {
    "result":  <numbers / rows>,
    "method":  "<one line: how this was computed>",
    "caveats": ["<assumption or limitation>", ...],
    "source":  "cache, fetched 2026-09-20" | "data.bts.gov live",
  }
The LLM is told to surface method + caveats; the UI shows source.

Tools (signatures per plan §5.1):
  rank_airports(region=None, state=None, tier=None, top_n=10, sort_by="cpi", baseline="yoy")
  airport_profile(code, refresh=False, baseline="yoy")     — refresh=True forces a live SODA fetch
  compare_airports(codes, baseline="yoy")                  — flags cross-tier comparisons
  long_haul_share(code)                                    — intl share of departures, intl share of ASM,
                                                             avg stage dom/intl, seats_per_intl_dep (cargo
                                                             heuristic), freight; states the definition
                                                             used and what it cannot compute
  unmet_demand(code)                                       — CPI decomposition; always includes pax_vs_2019
  explain_scoring(sensitivity=False)                       — CPI definition, weights, tiers, baseline options,
                                                             comparison rules; sensitivity table on request
  resolve_airport(name_or_city)                            — "LA" -> LAX with BUR/LGB/ONT/SNA listed as
                                                             alternatives; "Santa Ana" -> SNA

Every rank/compare/profile result includes recovered_2019 per airport so the agent can apply the
recovery rule (plan §5.3) without a second call.

Also exports TOOL_SCHEMAS: the JSON schemas handed to the Anthropic API for tool use.
"""
