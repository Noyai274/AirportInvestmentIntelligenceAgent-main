# Airport Investment Intelligence Agent — Design & Architecture

Amit · Deloitte Digital FDE take-home · September 2026

## 1. Problem

An investment firm funds airport modernisation where demand is pressing on existing capacity. It needs a chat agent that answers analyst questions ("which New England airports are candidates for terminal expansion?", "compare LA and Santa Ana congestion", "what is the unmet demand in SFO and why?") with numbers it can trace, a ranking logic it can defend, and honest limits. The design principle behind everything below: **the language model never computes a number.** Every figure comes from a deterministic Python function over a local table, and the function returns *how* it computed it alongside the result.

## 2. Data

**Source.** BTS T-100 *Segment Summary by Origin Airport* (Socrata dataset `r495-tyji`), queried through the SODA API (`data.bts.gov`) with server-side filters and paginated pulls. One row per airport-month: passengers, seats, departures, freight, average stage length and passenger distance, split domestic/international. January 2019 to April 2026 (BTS publishes with a 4–5 month lag); 79,486 rows, 1,319 US airports.

**Cache-through, not snapshot.** Tools read SQLite first and go to the API on a miss or when the full fetch is older than 30 days; every tool result carries a `source` stamp (`cache, fetched <date>` or `data.bts.gov live`), and `airport_profile(refresh=True)` forces a live pull. BTS updates monthly, so a month-old cache is current; caching removes BTS availability and rate limits from the demo's critical path without changing where the data comes from. A warmed `airports.db` is committed so the app runs offline; delete it to watch it fetch live.

**What it does not contain — and the agent says so.** No delays or on-time data. No gates, runways, terminals or security lanes: "seats" are aircraft seats offered by airlines, not terminal capacity, so pressure on the airport is *inferred from airline behaviour*, never observed. No carrier mix. Departure counts include all-cargo flights. Airport state comes from OurAirports, joined at read time.

## 3. Comparison methodology

Three rules so the ranking is not simply "the biggest airport wins":

1. **Ratios only.** Every scoring input is a ratio (load factor), a rate of change (year-over-year growth) or a spread between two rates (passenger growth minus seat growth). Never an absolute.
2. **Compare within hub tier.** FAA hub classes are computed from the data itself — share of total US passengers, Large ≥ 1 %, Medium ≥ 0.25 %, Small ≥ 0.05 % — and every z-score is taken *within* the tier (exactly 30 Large hubs come out, matching the FAA list). SFO is measured against other large hubs, not against Portland, Maine. Cross-tier results are flagged, and CPI is never quoted across tiers as one scale.
3. **Control for exogenous factors, or say you can't.** The data has no delay attribution, so the agent does not invent one. It names known structural constraints in one sentence labelled "outside the data" (SNA's settlement cap and curfew; slot controls at JFK, LGA, DCA; the FAA operating limits at EWR; ANC's freighter-refuelling role) and nothing else from memory.

## 4. The KPI — Capacity Pressure Index

The assignment says "your defined logic or KPI". I read it both ways: **18 per-airport indicators** (`scoring/config.py: KPIS` — growth, load factor and its trend, seat and departure growth, gauge, international shares, stage lengths, ASM/RPM, freight, 2019 recovery) that every answer can report, and **one ranking logic** built on four of them.

**What CPI measures:** how hard current demand presses on an airport's existing capacity, and whether that pressure is rising.

| Component | Definition | Why |
|---|---|---|
| `growth` | passengers, trailing 12 months vs the 12 before | demand is rising |
| `load_factor` | passengers ÷ seats, TTM, percent | the planes are full |
| `lf_trend` | load factor now minus a year ago, percentage points | and getting fuller |
| `demand_minus_supply` | passenger growth minus seat growth | airlines cannot add seats fast enough |

CPI = Σ 0.25 · z(component), each z computed within hub tier and clipped to ±3 so one airport with +40 % growth off a small base cannot dominate its tier. Equal weights are the *definition*, not an estimate: four ratios that each evidence the same thesis and no ground truth to prefer one.

**Size is deliberately not a component.** The first version had a fifth term, log(passengers), documented as a "stabiliser". An external review of the running app showed it was a size bonus: a within-tier z-score of size is a monotone "bigger ranks higher" term, and LAX — shrinking 3.3 % year-over-year and 15.7 % below 2019 — drew its *entire* positive score from it (rank 12 of 30). Removing it moved LAX to 19 of 30; the correlation between passengers and CPI inside the Large tier is now 0.29. Size still enters where it belongs: the tiers, the ranking floor, and `pax_ttm` reported with every answer.

**Choices backed by the data, not by taste:**

- *Ranking floor: 300,000 passengers TTM.* Standard deviation of year-over-year growth by size bucket: 0.21 at 100–300k, 0.08 at 300k–1M, 0.04 above 1M. Below 300k, growth is one carrier opening one route. 178 airports are scored; the rest still get their raw metrics, just no rank.
- *Complete windows only.* An airport is scored only when both its trailing and prior 12-month windows are complete.
- *Load factor on seats, not seat-miles.* Terminal capacity is consumed per passenger, not per passenger-mile; seat-based load factor is also BTS's published basis.
- *2019 is a flag, not the headline.* Terminals were sized for 2019 traffic, but 2019 is seven years old. The default baseline is year-over-year; every airport carries `recovered_2019` (true when within −2 % of 2019 — the median absolute year-over-year change across large hubs is 2.6 %, so −2 % is ordinary noise). When an answer includes a not-yet-recovered airport (9 of 30 large hubs), the agent says so and offers once to re-rank with `baseline="2019"`, which swaps only the growth term.
- *Direction, next to every score.* CPI measures pressure, not growth, and pressure has two sources: demand outrunning supply (SFO: +3.4 % passengers, load factor holding while peers' fall) and supply cut faster than demand (HNL: −1.9 % passengers, −3.7 % seats, load factor up 1.5 points). With most large hubs shrinking in the current window (median growth −1.4 %), both kinds reach the top. Every row therefore carries `direction` — expanding / flat / contracting on passenger growth, ±2 % band — and "candidates for expansion" questions lead with expanding airports: within the Large tier that list is SFO, MCO, IAD, ORD, SAN.
- *Sensitivity.* Rank within tier under three weight sets (equal / growth-heavy / utilisation-heavy): SFO 2/1/2, DEN 3/7/5, BOS 8/9/4, LAX 19/21/15, SNA 3/1/3, BDL 5/5/4. The table does not justify the weights; it shows how much the ranking depends on them — the top and bottom are stable, the middle moves.

## 5. Definitions — and each one's blind spot

- **Congestion** = capacity-utilisation proxy: load factor, demand-minus-supply, departure growth, with within-tier standing. *Blind spot:* it is not a delay measure; a slot-constrained airport (SNA, LGA) can look calm because airlines cannot add flights.
- **Unmet demand** = the CPI decomposition for one airport, with the three utilisation components summed as a sub-score. *Blind spot:* served demand only — people who did not fly because of fares or missing routes are invisible.
- **Long haul** has no standard definition. The tool states the one it uses and reports three proxies: international share of departures (counts freighters), international share of available seat-miles (weights by distance and seats; freighters contribute zero), and average stage length domestic vs international. *Blind spot:* a share of flights above a mileage threshold is not computable from airport-level averages. At Anchorage the two shares diverge — 15 % of departures, 3.5 % of seat-miles — because international departures average 4.4 seats (BOS: 204): freighters. The cargo flag (< 60 seats per international departure and ≥ 300 such departures) fires on ANC, MEM, SDF, IND, ILN, SBD — the known cargo hubs — and the agent asks whether the user means all flights or passenger flights.

## 6. Where AI is used — and where it is not

Claude (Sonnet 5, Anthropic Messages API with tool use) does four things: **selects** the tool for the question from eight typed schemas; **narrates** the result, leading with the finding; **handles ambiguity** — "LA" resolves to LAX with BUR/LGB/ONT/SNA listed as alternatives, "New York" to JFK/LGA/EWR; and **makes the recovery offer**. The system prompt carries the hard rules: every number comes from a tool result; name the tool and its caveats; units exactly as the payload gives them; never describe a flat or falling metric as rising because its within-tier z is positive; refuse gate, runway and delay questions and name the metric that would answer them.

Claude does **no arithmetic and no data fetching**. The eight tools (`rank_airports`, `airport_profile`, `compare_airports`, `unmet_demand`, `long_haul_share`, `explain_scoring`, `resolve_airport`, `trend`) are pure functions over the metrics table; each validates its input and returns the same envelope — `result`, `method`, `caveats`, `source`. The UI shows every call, its arguments, source line and caveats under the answer, outside the model's control. 37 tests run offline against the committed database: scoring invariants, one test per question in a 22-question analyst bank, and the tool-use loop with a stub client (including truncation and continuation).

## 7. Key tradeoffs

| Chose | Over | Because |
|---|---|---|
| Cache-through SQLite | pure live API | monthly data; demo must survive BTS being down; source is still the API |
| One dataset (T-100) | T-100 + On-Time + ACI | one clean contract beats three joins in a week; the gaps are named, not papered over |
| Airport-level averages | segment-level T-100 | 80k rows vs millions; the cost is the long-haul threshold, stated |
| Within-tier z-scores | one national ranking | fair peer sets; the cost is small tiers and no cross-tier scale, stated |
| Equal weights + sensitivity table | fitted weights | no ground truth to fit against; transparency over false precision |
| Streamlit | a real frontend | the expander under each answer is the demo; a frontend would hide it |

## 8. With another week

1. **Attributed delay data** — BTS On-Time Performance (`CarrierDelay`, `WeatherDelay`, `NASDelay`) so "congestion" can mean congestion and exogenous causes can be separated, not just named.
2. **Segment-level T-100** for a true long-haul distance distribution.
3. **Passengers per gate** from FAA/ACI facility data — the metric the "terminal expansion" question actually wants.
4. Answer-level evaluation against the question bank: caveats returned vs printed, denominators present in payloads, `stop_reason` logged.
