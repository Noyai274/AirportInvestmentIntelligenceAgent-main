# Design & Architecture

<!--
Owner: Amit. Plan §7. The exam deliverable: "short design/architecture document explaining
scoring methodology, key tradeoffs, where/how AI is used". Two pages.

Outline (one section each — see plan §7 for what goes in every section):
  1. Problem framing — three sentences.
  2. Data — BTS T-100 via SODA (dataset r495-tyji), through Apr 2026, what it does not contain
     (no delays, no gates, no carrier mix; departures include freighters).
  3. Comparison methodology — the three rules from plan §4.0: ratios only, within-hub-tier
     z-scores, exogenous factors named or declared unmeasurable.
  4. The KPI — Capacity Pressure Index: what it measures, five components (one sentence each),
     equal weights as the definition, hub tiers, the empirically chosen ranking floor (with chart),
     seat-based load factor and why, the baseline switch (YoY vs 2019) and why 2019 is a flag not
     a headline, the sensitivity table.
  5. Definitions — congestion (utilisation proxy), unmet demand (served demand only), long-haul
     (no standard definition; three proxies; cargo heuristic) — and each one's blind spot.
  6. Where AI is used (tool selection, narration, ambiguity, the recovery offer) and where it
     deliberately is not (arithmetic, data fetching).
  7. Key tradeoffs — cache-through vs live; one source vs many; averages vs segment-level;
     Streamlit vs a real frontend.
  8. With another week — attributed delay data (BTS On-Time Performance: CarrierDelay /
     WeatherDelay / NASDelay) first; segment-level T-100 for a true long-haul distribution second.
-->
